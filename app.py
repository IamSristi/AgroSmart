"""
AgriNova UNIFIED — Flask Backend
============================================================

MODE LOGIC:
  ONLINE  → ESP's offlineMode==false → Flask reads from Firebase
  OFFLINE → ESP's offlineMode==true  → Flask proxies to ESP HTTP

AI & VISION ENGINE:
  Multi-provider engine supporting Ollama (native + OpenAI API)
  and local Llamafile/Llama.cpp vision models with automatic discovery.
"""

import os, re, json, base64, io, time, threading
import requests
from datetime import datetime

from flask import Flask, request, render_template, jsonify, send_from_directory, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdfcanvas
from dotenv import load_dotenv
from PIL import Image

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

from leaf_detector import detect_and_mark_leaf_spots

yolo_model = None
YOLO_MODEL_PATH = None

load_dotenv(override=True)

# ─────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────

AI_PROVIDER      = os.getenv("AI_PROVIDER", "auto")
AI_BASE_URL      = os.getenv("AI_BASE_URL", "http://****")
AI_MODEL         = os.getenv("AI_MODEL", "Qwen3VL-2B-Instruct-Q4_K_M.gguf")
OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://****")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "llama3.2-vision")

FIREBASE_DB_URL  = os.getenv("FIREBASE_DB_URL", "https://hahahahahah")
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "cannot give you, sorry")
FIREBASE_EMAIL   = os.getenv("FIREBASE_EMAIL", "****@gmail.com")
FIREBASE_PASS    = os.getenv("FIREBASE_PASS", "########")

APP_USER_EMAIL = os.getenv("APP_USER_EMAIL", "****@gmail.com")
APP_USER_PASS  = os.getenv("APP_USER_PASS", "########")

ESP_BASE     = os.getenv("ESP_IP", "http://****")
ESP_TIMEOUT  = 4      # seconds for sensor/pump requests
MODE_TIMEOUT = 3     # seconds for /mode poll

app = Flask(__name__)
app.secret_key = os.getenv("Secret key", "****")
CORS(app)
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["REPORT_FOLDER"] = "reports"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["REPORT_FOLDER"], exist_ok=True)

# ─────────────────────────────────────────────────────────────────────
#  FIREBASE AUTH
# ─────────────────────────────────────────────────────────────────────

_fb_token     = None
_fb_token_exp = 0

def get_firebase_token():
    global _fb_token, _fb_token_exp
    if _fb_token and time.time() < _fb_token_exp:
        return _fb_token
    try:
        r = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
            f"?key={FIREBASE_API_KEY}",
            json={"email": FIREBASE_EMAIL, "password": FIREBASE_PASS, "returnSecureToken": True},
            timeout=10
        )
        r.raise_for_status()
        d = r.json()
        _fb_token     = d["idToken"]
        _fb_token_exp = time.time() + int(d.get("expiresIn", 3600)) - 60
        return _fb_token
    except Exception as e:
        print(f"[Firebase auth] {e}")
        return None

def fb_get(path):
    try:
        token = get_firebase_token()
        suffix = f"?auth={token}" if token else ""
        r = requests.get(f"{FIREBASE_DB_URL}/{path}.json{suffix}", timeout=8)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)

def fb_set(path, value):
    try:
        token = get_firebase_token()
        suffix = f"?auth={token}" if token else ""
        r = requests.put(f"{FIREBASE_DB_URL}/{path}.json{suffix}", json=value, timeout=8)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)

def fb_update(path, data):
    try:
        token = get_firebase_token()
        suffix = f"?auth={token}" if token else ""
        r = requests.patch(f"{FIREBASE_DB_URL}/{path}.json{suffix}", json=data, timeout=8)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)

# ─────────────────────────────────────────────────────────────────────
#  ESP PROXY HELPERS
# ─────────────────────────────────────────────────────────────────────

def esp_get(path):
    try:
        r = requests.get(ESP_BASE + path, timeout=ESP_TIMEOUT)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Cannot reach ESP8266. Is laptop connected to AgriNova WiFi?"
    except Exception as e:
        return None, str(e)

def esp_post(path, payload):
    try:
        r = requests.post(ESP_BASE + path, json=payload, timeout=ESP_TIMEOUT,
                          headers={"Content-Type": "application/json"})
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Cannot reach ESP8266. Is laptop connected to AgriNova WiFi?"
    except Exception as e:
        return None, str(e)

# ─────────────────────────────────────────────────────────────────────
#  MODE CACHE
# ─────────────────────────────────────────────────────────────────────

_cached_mode       = "online"
_cached_ap_clients = 0
_cached_sta_up     = True
_esp_reachable     = False
_mode_lock         = threading.Lock()
_MODE_POLL_SEC     = 2

def _mode_refresh_loop():
    global _cached_mode, _cached_ap_clients, _cached_sta_up, _esp_reachable
    fail_count         = 0
    offline_fail_start = 0.0
    ESP_OFFLINE_TIMEOUT_SEC = 8

    while True:
        try:
            r = requests.get(ESP_BASE + "/mode", timeout=MODE_TIMEOUT)
            if r.ok:
                d = r.json()
                with _mode_lock:
                    _cached_mode       = d.get("mode", _cached_mode)
                    _cached_ap_clients = d.get("clients", 0)
                    _cached_sta_up     = d.get("staUp", True)
                    _esp_reachable     = True
                fail_count         = 0
                offline_fail_start = 0.0
        except Exception:
            fail_count += 1
            with _mode_lock:
                _esp_reachable = False
                current_mode   = _cached_mode

            if current_mode == "offline":
                if offline_fail_start == 0.0:
                    offline_fail_start = time.time()
                elapsed = time.time() - offline_fail_start
                if elapsed >= ESP_OFFLINE_TIMEOUT_SEC:
                    with _mode_lock:
                        _cached_mode = "online"
                    offline_fail_start = 0.0
                    fail_count         = 0
        time.sleep(_MODE_POLL_SEC)

threading.Thread(target=_mode_refresh_loop, daemon=True, name="mode-poll").start()

def get_mode():
    with _mode_lock:
        return _cached_mode

@app.route("/api/mode")
def api_mode():
    with _mode_lock:
        mode    = _cached_mode
        clients = _cached_ap_clients
        sta_up  = _cached_sta_up
        reachable = _esp_reachable
    return jsonify({"mode": mode, "apClients": clients, "staUp": sta_up,
                    "espReachable": reachable})

# ─────────────────────────────────────────────────────────────────────
#  SENSOR DATA
# ─────────────────────────────────────────────────────────────────────

@app.route("/api/sensors")
def api_sensors():
    mode = get_mode()
    if mode == "offline":
        data, err = esp_get("/sensors")
        if err: return jsonify({"error": err}), 503
        data["mode"] = "offline"
        return jsonify(data)

    sensors, err = fb_get("sensors")
    if err: return jsonify({"error": f"Firebase: {err}"}), 503
    controls, _ = fb_get("controls")
    return jsonify({
        "mode":            "online",
        "waterLevelPct":   sensors.get("waterLevelPercent",  0),
        "waterDistanceCm": sensors.get("waterLevelDistance", 0),
        "tankEmptyHeight": sensors.get("tankEmptyHeight",    30),
        "moisture":        sensors.get("moisture",           0),
        "temperature":     sensors.get("temperature",        0),
        "humidity":        sensors.get("humidity",           0),
        "ph":              sensors.get("ph",                 7.0),
        "pump1":           bool((controls or {}).get("pump1", False)),
        "pump2":           bool((controls or {}).get("pump2", False)),
        "sensorNodeOk":    sensors.get("sensorNodeOk",       False),
    })

# ─────────────────────────────────────────────────────────────────────
#  PUMP CONTROL & CALIBRATION
# ─────────────────────────────────────────────────────────────────────

@app.route("/api/pump1", methods=["POST"])
def api_pump1():
    mode  = get_mode()
    state = bool((request.get_json(force=True, silent=True) or {}).get("state", False))
    if mode == "offline":
        data, err = esp_post("/pump1", {"state": state})
        if err: return jsonify({"error": err}), 503
        return jsonify(data)
    val, err = fb_set("controls/pump1", 1 if state else 0)
    if err: return jsonify({"error": err}), 503
    return jsonify({"ok": True, "pump1": state})

@app.route("/api/pump2", methods=["POST"])
def api_pump2():
    mode  = get_mode()
    state = bool((request.get_json(force=True, silent=True) or {}).get("state", False))
    if mode == "offline":
        data, err = esp_post("/pump2", {"state": state})
        if err: return jsonify({"error": err}), 503
        return jsonify(data)
    val, err = fb_set("controls/pump2", 1 if state else 0)
    if err: return jsonify({"error": err}), 503
    return jsonify({"ok": True, "pump2": state})

@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    data, err = esp_post("/calibrate", {})
    if err: return jsonify({"error": "ESP unreachable: " + err}), 503
    return jsonify(data)

@app.route("/api/soil", methods=["POST"])
def api_soil():
    body     = request.get_json(force=True, silent=True) or {}
    moisture = int(body.get("moisture", 0))
    mode     = get_mode()
    if mode == "offline":
        data, err = esp_post("/soil", {"moisture": moisture})
        if err: return jsonify({"error": err}), 503
        return jsonify(data)
    val, err = fb_update("sensors", {"moisture": moisture})
    if err: return jsonify({"error": err}), 503
    return jsonify({"ok": True, "moisture": moisture})

# ─────────────────────────────────────────────────────────────────────
#  MULTI-PROVIDER AI ENGINE (OLLAMA + LLAMAFILE + OPENAI)
# ─────────────────────────────────────────────────────────────────────

def get_ai_candidate_endpoints():
    """Returns candidate AI endpoints to check in order."""
    endpoints = []
    
    # 1. Ollama endpoints
    ollama_url = OLLAMA_BASE_URL.rstrip("/") if OLLAMA_BASE_URL else "http://****"
    endpoints.append({"provider": "ollama", "url": ollama_url, "default_model": OLLAMA_MODEL})
    if "127.0.0.1" not in ollama_url and "localhost" not in ollama_url:
        endpoints.append({"provider": "ollama", "url": "http://****", "default_model": "llama3.2-vision"})
    
    # 2. Local Llamafile / Llama.cpp / OpenAI endpoints
    ai_url = AI_BASE_URL.rstrip("/") if AI_BASE_URL else "http://****"
    endpoints.append({"provider": "llamafile", "url": ai_url, "default_model": AI_MODEL})
    if "127.0.0.1" not in ai_url and "8080" not in ai_url:
        endpoints.append({"provider": "llamafile", "url": "http://****", "default_model": "Qwen3VL-2B-Instruct-Q4_K_M.gguf"})
        
    return endpoints

_ai_cache_time = 0
_ai_cache_data = None

def detect_active_ai_engine(force_refresh=False):
    """Discovers which AI engine (Ollama or Llama.cpp) is online and active."""
    global _ai_cache_time, _ai_cache_data
    if not force_refresh and _ai_cache_data and (time.time() - _ai_cache_time < 10):
        return _ai_cache_data

    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "AgriNova"}
    candidates = get_ai_candidate_endpoints()

    for cand in candidates:
        provider = cand["provider"]
        base_url = cand["url"]
        
        # Check Ollama
        if provider == "ollama":
            try:
                r = requests.get(f"{base_url}/api/tags", headers=headers, timeout=2)
                if r.ok:
                    data = r.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    chosen = cand["default_model"]
                    # If model exists or find best vision model
                    vision_models = [m for m in models if any(k in m.lower() for k in ["vision", "vl", "llava", "minicpm", "moondream"])]
                    if vision_models:
                        chosen = vision_models[0]
                    elif models and chosen not in models:
                        chosen = models[0]
                        
                    result = {
                        "online": True,
                        "provider": "ollama",
                        "engine_name": f"Ollama ({chosen})",
                        "base_url": base_url,
                        "model": chosen,
                        "available_models": models,
                        "vision_capable": bool(vision_models or "vision" in chosen.lower() or "vl" in chosen.lower() or "llava" in chosen.lower())
                    }
                    _ai_cache_time = time.time()
                    _ai_cache_data = result
                    return result
            except Exception:
                pass

        # Check OpenAI-compatible / Llamafile / Llama.cpp
        try:
            r = requests.get(f"{base_url}/v1/models", headers=headers, timeout=2)
            if r.ok:
                data = r.json()
                models = []
                if "data" in data and isinstance(data["data"], list):
                    models = [m.get("id") for m in data["data"] if isinstance(m, dict)]
                elif "models" in data and isinstance(data["models"], list):
                    models = [m.get("name") for m in data["models"] if isinstance(m, dict)]
                
                chosen = cand["default_model"]
                if models and chosen not in models:
                    chosen = models[0]
                    
                result = {
                    "online": True,
                    "provider": "llamafile",
                    "engine_name": f"Vision AI ({chosen})",
                    "base_url": base_url,
                    "model": chosen,
                    "available_models": models,
                    "vision_capable": True
                }
                _ai_cache_time = time.time()
                _ai_cache_data = result
                return result
        except Exception:
            pass

    result = {
        "online": False,
        "provider": "none",
        "engine_name": "AI Engine Offline",
        "base_url": None,
        "model": None,
        "available_models": [],
        "vision_capable": False
    }
    _ai_cache_time = time.time()
    _ai_cache_data = result
    return result

def is_ai_online():
    return detect_active_ai_engine()["online"]

@app.route("/api/ai-status")
def api_ai_status():
    status = detect_active_ai_engine(force_refresh=True)
    return jsonify(status)

def call_ai_service(messages, max_tokens=800, image_path=None):
    """Unified multi-engine caller for Ollama and OpenAI/Llamafile."""
    engine = detect_active_ai_engine()
    if not engine["online"]:
        print(f"[AI Service] Offline! Checked endpoints: {get_ai_candidate_endpoints()}", flush=True)
        return "ERROR: AI service is currently offline. Please start Ollama or llamafile."

    provider = engine["provider"]
    base_url = engine["base_url"]
    model = engine["model"]
    
    print(f"\n[AI Service] Querying {provider.upper()} ({model}) at {base_url}...", flush=True)

    # Prepare and optimize image if present
    img_b64 = None
    if image_path and os.path.exists(image_path):
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                img.thumbnail((640, 640))
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=85)
                img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        except Exception as e:
            print(f"[AI Service] Image optimization error: {e}", flush=True)

    try:
        start_time = time.time()
        
        # 1. Ollama Native API Path
        if provider == "ollama":
            url = f"{base_url}/api/chat"
            ollama_msgs = []
            for m in messages:
                msg_obj = {"role": m["role"], "content": m["content"]}
                if m["role"] == "user" and img_b64:
                    msg_obj["images"] = [img_b64]
                ollama_msgs.append(msg_obj)
            
            payload = {
                "model": model,
                "messages": ollama_msgs,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": max_tokens
                }
            }
            resp = requests.post(url, json=payload, timeout=180)
            if resp.ok:
                ans = resp.json().get("message", {}).get("content", "")
                print(f"[AI Service] Ollama response received in {time.time() - start_time:.1f}s", flush=True)
                return ans

        # 2. OpenAI-compatible / Llamafile / Llama.cpp Path
        final_messages = []
        for m in messages:
            final_messages.append(m.copy())

        if img_b64:
            last_msg = final_messages[-1]
            user_text = last_msg.get("content", "")
            last_msg["content"] = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]

        payload = {
            "model": model,
            "messages": final_messages,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": 0.1
        }
        url = f"{base_url}/v1/chat/completions"
        resp = requests.post(url, json=payload, timeout=180)
        resp.raise_for_status()
        ans = resp.json()["choices"][0]["message"]["content"]
        print(f"[AI Service] Vision response received in {time.time() - start_time:.1f}s", flush=True)
        return ans

    except requests.exceptions.Timeout:
        return "ERROR: AI processing timed out. Try a simpler image or check server load."
    except Exception as e:
        print(f"[AI Service] ERROR: {e}", flush=True)
        return f"AI Service Error: {e}."

def unified_call_ai(messages, max_tokens=700, image_path=None):
    return call_ai_service(messages, max_tokens, image_path)

def extract_json_response(raw):
    """Robustly cleans and extracts a JSON dictionary from LLM text."""
    if not raw or raw.startswith("ERROR:"):
        return None
    s = raw.strip()
    s = re.sub(r"```(?:json)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"```", "", s).strip()

    start = s.find('{')
    end = s.rfind('}')
    if start != -1 and end != -1:
        s = s[start:end+1]

    # Remove trailing commas
    s = re.sub(r",\s*([\]}])", r"\1", s)

    try:
        return json.loads(s)
    except Exception:
        # Try closing missing brackets/quotes
        open_b, close_b = s.count('{'), s.count('}')
        if s.count('"') % 2 != 0:
            s += '"'
        for _ in range(max(0, open_b - close_b)):
            s += '}'
        try:
            return json.loads(s)
        except Exception:
            return None

def match_crop_profile(plant_name):
    """Matches the diagnosed plant name with system crop profiles."""
    if not plant_name:
        return "general", {}
    
    p = plant_name.lower()
    try:
        t_path = os.path.join(os.path.dirname(__file__), "thresholds.json")
        if os.path.exists(t_path):
            with open(t_path, "r") as f:
                thresholds = json.load(f)
                
                # Check direct or substring match
                for key in thresholds.keys():
                    key_clean = key.replace("_", " ").lower()
                    if key_clean in p or p in key_clean:
                        return key, thresholds[key]
                        
                # Common mappings
                mappings = {
                    "tomato": "tomato",
                    "potato": "potato",
                    "rice": "rice",
                    "paddy": "rice",
                    "wheat": "wheat",
                    "corn": "maize",
                    "maize": "maize",
                    "grape": "grapes",
                    "apple": "apple",
                    "cotton": "cotton",
                    "tea": "tea",
                    "coffee": "coffee",
                    "chili": "chili",
                    "pepper": "chili",
                    "capsicum": "chili",
                    "cucumber": "cucumber",
                    "brinjal": "brinjal",
                    "eggplant": "brinjal",
                    "onion": "onion",
                    "garlic": "garlic",
                    "mango": "mango",
                    "banana": "banana",
                    "citrus": "citrus",
                    "lemon": "citrus",
                    "orange": "citrus",
                    "mustard": "mustard",
                    "soybean": "soybean",
                    "blueberry": "general"
                }
                for word, crop_key in mappings.items():
                    if word in p:
                        return crop_key, thresholds.get(crop_key, thresholds.get("general", {}))
                
                return "general", thresholds.get("general", {})
    except Exception as e:
        print(f"[Crop Matcher] {e}")
    return "general", {}

# ─────────────────────────────────────────────────────────────────────
#  LEAF VISION ANALYSIS
# ─────────────────────────────────────────────────────────────────────

def run_vision_analysis(image_path, plant_name=None):
    plant_context = f"The user identifies/corrects this plant species as: {plant_name}." if plant_name else ""

    # 1. Computer Vision Spot Detection
    print(f"\n[Leaf Spot Detector] Analyzing spots and pathology contours for {image_path}...", flush=True)
    try:
        cv_results = detect_and_mark_leaf_spots(image_path)
    except Exception as e:
        print(f"[Leaf Spot Detector] CV Error: {e}", flush=True)
        cv_results = {
            "marked_image": None,
            "heatmap_image": None,
            "mask_image": None,
            "spot_count": 0,
            "affected_area_pct": 0,
            "healthy_area_pct": 100,
            "spots": [],
            "dominant_symptom": "Unknown",
            "symptom_breakdown": {}
        }

    spot_count = cv_results.get("spot_count", 0)
    affected_pct = cv_results.get("affected_area_pct", 0)
    dominant_symptom = cv_results.get("dominant_symptom", "Healthy")

    # 2. Vision AI Multimodal Diagnosis
    prompt = f"""ACT AS A SENIOR PLANT PATHOLOGIST AND BOTANIST.
Analyze this leaf photograph with high precision:
1. Identify the EXACT plant/crop species name (e.g. Tomato, Potato, Apple, Blueberry, Rice, Wheat, Corn, Cotton, Pepper, Rose, Grape, Cucumber, Citrus, etc.).
2. Scientific botanical name (e.g. Solanum lycopersicum, Vaccinium corymbosum, Oryza sativa, etc.).
3. Specific disease diagnosis or confirm if Healthy Plant Leaf.
4. Severity level (Mild, Moderate, Severe, or None).
5. Confidence percentage (e.g. 95%).
6. Concise pathological cause (1-2 sentences).
7. Actionable organic treatment and fungicide recommendations (1-2 sentences).
8. Preventive cultural practices (1-2 sentences).

OPTICAL SPOT DETECTION METRICS:
- Disease spots/lesions marked: {spot_count}
- Damaged leaf surface: {affected_pct}%
- Dominant symptom: {dominant_symptom}
{plant_context}

Respond ONLY with a valid JSON object matching this schema:
{{
  "plant": "Common Plant Name",
  "botanical_name": "Scientific Botanical Name",
  "disease": "Specific Disease Name or Healthy Plant Leaf",
  "severity": "Mild/Moderate/Severe/None",
  "confidence": "95%",
  "cause": "Clear 1-2 sentence pathological cause",
  "recommendation": "Clear 1-2 sentence organic treatment advice",
  "prevention": "Clear 1-2 sentence cultural prevention measures"
}}"""

    ai = None
    try:
        raw = call_ai_service([{"role": "user", "content": prompt}], 700, image_path=image_path)
        if raw and not raw.startswith("ERROR:"):
            ai = extract_json_response(raw)
            if not ai:
                print(f"[AI Analyzer] Raw non-JSON output: {raw}", flush=True)
    except Exception as e:
        print(f"[AI Analyzer] Exception: {e}", flush=True)

    # 3. Fallback if AI service is offline
    if not ai or not isinstance(ai, dict):
        engine_info = detect_active_ai_engine()
        if spot_count > 0:
            sev = "Severe" if affected_pct > 20 else ("Moderate" if affected_pct > 8 else "Mild")
            disease_name = f"{dominant_symptom} Lesions" if dominant_symptom != "Healthy" else "Leaf Spot Infection"
            cause_text = f"Detected {spot_count} pathological spots covering {affected_pct}% of the leaf surface, indicating fungal or bacterial foliar damage."
            rec_text = "Prune severely affected leaves and apply organic copper-based fungicide or cold-pressed neem oil spray."
            prev_text = "Ensure adequate spacing for air circulation, avoid overhead sprinkler watering, and maintain balanced soil nutrition."
        else:
            sev = "None"
            disease_name = "Healthy Plant Leaf"
            cause_text = "No necrotic lesions or significant chlorotic spots were detected across the leaf surface."
            rec_text = "No chemical or organic fungicide treatment is needed. Continue standard watering schedule."
            prev_text = "Maintain optimal soil moisture and inspect regularly for early pest or disease symptoms."

        ai = {
            "plant": plant_name or "Identified Foliage",
            "botanical_name": "Plantae Species",
            "disease": disease_name,
            "severity": sev,
            "confidence": "85% (CV Engine)",
            "cause": cause_text,
            "recommendation": rec_text,
            "prevention": prev_text,
            "ai_offline": not engine_info["online"]
        }

    # Normalize fields and reconcile with CV findings
    identified_plant = ai.get("plant") or plant_name or "Plant Leaf"
    botanical_name = ai.get("botanical_name") or "Plantae Species"
    disease_name = ai.get("disease") or "Healthy Leaf"
    severity_val = ai.get("severity") or "Mild"
    confidence_val = ai.get("confidence") or "90%"
    
    # Reconcile AI text with optical spot detection ground truth
    if spot_count > 0 and affected_pct >= 1.5:
        calc_sev = "Severe" if affected_pct > 18 else ("Moderate" if affected_pct > 6 else "Mild")
        if severity_val in ["None", "Healthy", "none", ""]:
            severity_val = calc_sev
        if "healthy" in disease_name.lower():
            disease_name = f"{dominant_symptom} Foliar Lesions" if dominant_symptom not in ["Healthy", "Unknown"] else "Leaf Spot Pathology"
    elif spot_count == 0 and affected_pct < 1.0:
        severity_val = "None"
        if "lesion" in disease_name.lower() or "infection" in disease_name.lower():
            disease_name = "Healthy Plant Leaf"

    crop_key, crop_thresholds = match_crop_profile(identified_plant)
    
    response_data = {
        **ai,
        "plant": identified_plant,
        "botanical_name": botanical_name,
        "disease": disease_name,
        "severity": severity_val,
        "confidence": confidence_val,
        "recommendation": ai.get("recommendation") or ai.get("suggestion", "Apply organic treatment as needed."),
        "prevention": ai.get("prevention", "Maintain balanced soil moisture and air circulation."),
        "crop_key": crop_key,
        "crop_thresholds": crop_thresholds,
        **cv_results
    }

    return jsonify(response_data)

# ─────────────────────────────────────────────────────────────────────
#  ROUTES (Analyze, Correct, Chat, Thresholds, Reports)
# ─────────────────────────────────────────────────────────────────────

_last_image_path = None

@app.route("/analyze", methods=["POST"])
def analyze():
    global _last_image_path
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(f.filename))
    f.save(path)
    _last_image_path = path
    return run_vision_analysis(path)

@app.route("/correct-plant", methods=["POST"])
def correct_plant():
    global _last_image_path
    plant = (request.get_json(force=True, silent=True) or {}).get("plant", "").strip()
    if not plant or not _last_image_path:
        return jsonify({"error": "No previous analysis found. Please upload a leaf first."}), 400
    return run_vision_analysis(_last_image_path, plant_name=plant)

def get_current_context(crop_id="general"):
    context = {"sensors": {}, "crop": crop_id, "thresholds": {}}
    mode = get_mode()
    if mode == "offline":
        data, _ = esp_get("/sensors")
        if data:
            context["sensors"] = {"temp": data.get("temperature"), "hum": data.get("humidity"), 
                                  "moisture": data.get("moisture"), "water_level": data.get("waterLevelPct"),
                                  "ph": data.get("ph")}
    else:
        sensors, _ = fb_get("sensors")
        if sensors:
            context["sensors"] = {"temp": sensors.get("temperature"), "hum": sensors.get("humidity"),
                                  "moisture": sensors.get("moisture"), "water_level": sensors.get("waterLevelPercent"),
                                  "ph": sensors.get("ph")}
    try:
        path = os.path.join(os.path.dirname(__file__), "thresholds.json")
        if os.path.exists(path):
            with open(path, "r") as f:
                thresholds = json.load(f)
                context["available_crops"] = list(thresholds.keys())
                context["thresholds"] = thresholds.get(crop_id, thresholds.get("general", {}))
    except Exception:
        pass
    return context

@app.route("/ask", methods=["POST"])
def ask():
    data      = request.get_json(force=True, silent=True) or {}
    msg       = data.get("message", "")
    lang_code = data.get("lang", "en-IN")
    crop_id   = data.get("crop", "general")
    ctx       = get_current_context(crop_id)
    
    sensor_summary = "N/A"
    if ctx["sensors"]:
        s = ctx["sensors"]
        sensor_summary = f"Temp: {s.get('temp')}°C, Hum: {s.get('hum')}%, Soil Moisture: {s.get('moisture')}%, Water Level: {s.get('water_level')}%, Soil pH: {s.get('ph')}"
    
    crop_thresholds = ""
    if ctx["thresholds"]:
        t = ctx["thresholds"]
        crop_thresholds = f"Ideal ranges for {crop_id}: Temp {t.get('temp',{}).get('label')}, Hum {t.get('hum',{}).get('label')}, Soil {t.get('soil',{}).get('label')}."

    lang_map = {"hi-IN": "Hindi", "bn-IN": "Bengali", "en-IN": "English"}
    target_lang = lang_map.get(lang_code, "English")

    if "chat_history" not in session:
        session["chat_history"] = []
    history = session["chat_history"][-10:]

    system_instr = (
        f"You are Kalpataru, an expert AI farming consultant. You MUST respond ONLY in {target_lang}.\n"
        f"LIVE SENSOR DATA: {sensor_summary}\n"
        f"ACTIVE CROP ({crop_id}): {crop_thresholds}\n"
        f"1. Respond using {target_lang} script. DO NOT use English if the language is Hindi or Bengali.\n"
        "2. Give direct, actionable farming facts and concise advice.\n"
        "3. If a sensor value or treatment is requested, reply directly and clearly.\n"
        "4. Maximum length: 25 words.\n"
        "5. If asked to switch crops, use 'switch mode <crop_id>'."
    )
    messages = [{"role": "system", "content": system_instr}]
    for h in history:
        messages.append(h)
    messages.append({"role": "user", "content": msg})

    try:
        reply = unified_call_ai(messages, 400)
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})
        session["chat_history"] = history
        session.modified = True
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"AI unavailable: {e}"}), 500

@app.route("/api/chat-history")
def get_chat_history():
    return jsonify(session.get("chat_history", []))

@app.route("/api/chat-clear", methods=["POST"])
def clear_chat():
    session["chat_history"] = []
    session.modified = True
    return jsonify({"ok": True})

@app.route("/logout", methods=["POST"])
def api_logout():
    session.pop("agrinova_auth", None)
    return jsonify({"ok": True})

@app.route("/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json(force=True) or {}
        email = data.get("email", "").strip()
        password = data.get("password", "")
        if email == APP_USER_EMAIL and password == APP_USER_PASS:
            session["agrinova_auth"] = True
            return jsonify({"ok": True})
        return jsonify({"error": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/download-report", methods=["POST"])
def download_report():
    try:
        data = request.get_json(force=True) or {}
        leaf = data.get("leaf", {})
        filepath = os.path.join(app.config["REPORT_FOLDER"], "report.pdf")
        c = pdfcanvas.Canvas(filepath, pagesize=A4)
        W, H = A4
        
        # Header Banner
        c.setFillColorRGB(0.03, 0.06, 0.12)
        c.rect(0, H - 90, W, 90, fill=True, stroke=False)
        c.setFillColorRGB(0.0, 1.0, 0.76)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(45, H - 45, "AgriNova — Leaf Pathology & Spot Diagnostic Report")
        c.setFillColorRGB(0.6, 0.75, 0.9)
        c.setFont("Helvetica", 10)
        c.drawString(45, H - 65, f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Engine: Ollama / Vision AI")

        # Section 1: Plant & Disease Identification
        y = H - 120
        c.setFillColorRGB(0.0, 0.8, 0.9)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(45, y, "1. BOTANICAL & DIAGNOSTIC SUMMARY")
        
        y -= 20
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.setFont("Helvetica", 10)
        c.drawString(55, y, f"• Identified Plant: {leaf.get('plant', 'Plant Leaf')} ({leaf.get('botanical_name', 'Plantae')})")
        y -= 16
        c.drawString(55, y, f"• Disease / Diagnosis: {leaf.get('disease', 'Foliar Health Analysis')}")
        y -= 16
        c.drawString(55, y, f"• Severity Level: {leaf.get('severity', 'Mild')}  |  Confidence: {leaf.get('confidence', '90%')}")

        # Section 2: Spot & Lesion Metrics
        y -= 26
        c.setFillColorRGB(0.0, 0.8, 0.9)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(45, y, "2. COMPUTER VISION SPOT DETECTION METRICS")
        
        y -= 20
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.setFont("Helvetica", 10)
        c.drawString(55, y, f"• Total Disease Spots Marked: {leaf.get('spot_count', 0)} lesions detected")
        y -= 16
        c.drawString(55, y, f"• Affected Leaf Surface: {leaf.get('affected_area_pct', 0)}% damaged")
        y -= 16
        c.drawString(55, y, f"• Healthy Tissue Area: {leaf.get('healthy_area_pct', 100)}%")
        y -= 16
        c.drawString(55, y, f"• Dominant Symptom: {leaf.get('dominant_symptom', 'Necrotic Lesions')}")

        # Section 3: Pathology Cause & Treatment
        y -= 26
        c.setFillColorRGB(0.0, 0.8, 0.9)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(45, y, "3. PATHOLOGY & RECOMMENDED ACTIONS")

        y -= 20
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(55, y, "Pathological Cause:")
        c.setFont("Helvetica", 9)
        y -= 15
        c.drawString(65, y, str(leaf.get('cause', 'Foliar spot damage detected by optical imaging.'))[:100])

        y -= 20
        c.setFont("Helvetica-Bold", 10)
        c.drawString(55, y, "Organic Treatment Recommendation:")
        c.setFont("Helvetica", 9)
        y -= 15
        c.drawString(65, y, str(leaf.get('recommendation', 'Apply organic copper fungicide or neem oil spray.'))[:100])

        y -= 20
        c.setFont("Helvetica-Bold", 10)
        c.drawString(55, y, "Prevention Guidelines:")
        c.setFont("Helvetica", 9)
        y -= 15
        c.drawString(65, y, str(leaf.get('prevention', 'Ensure good aeration and avoid wet foliage.'))[:100])

        # Footer
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(45, 40, "AgriNova Unified Smart Agriculture System — Automated Foliar Analysis Report")

        c.save()
        return jsonify({"file": "report.pdf", "ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/get-report/<filename>")
def get_report(filename):
    return send_from_directory(app.config["REPORT_FOLDER"], filename)

@app.route("/api/thresholds")
def get_thresholds():
    try:
        path = os.path.join(os.path.dirname(__file__), "thresholds.json")
        with open(path, "r") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/thresholds", methods=["POST"])
def update_thresholds():
    data = request.get_json(force=True, silent=True)
    try:
        path = os.path.join(os.path.dirname(__file__), "thresholds.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debug-yolo")
def debug_yolo():
    global yolo_model
    status = {
        "loaded": yolo_model is not None,
        "path": YOLO_MODEL_PATH,
        "available": YOLO_AVAILABLE,
        "classes": yolo_model.names if yolo_model else {}
    }
    return jsonify(status)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/weather")
def weather():
    return render_template("weather.html")

@app.route("/leaf")
def leaf():
    return render_template("leaf.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
