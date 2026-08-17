<div align="center">

<img src="https://raw.githubusercontent.com/IamSristi/AgriNova/main/screenshots/dashboard.png" width="0" height="0" alt=""/>

<!-- animated title -->
<a href="https://github.com/IamSristi/AgriNova">
  <img src="agrosmart-plant-animation-weather.svg" width="700" alt="AgroSmart animated banner"/>
</a>

<p>

**IoT • AI • Cloud — a complete precision-farming platform**
built by Team Innovengers for our B.Tech Final Year Project

</p>

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=14&duration=2500&pause=800&color=8B8B8B&center=true&vCenter=true&width=600&lines=Real-time+sensing+%7C+Automated+irrigation+%7C+AI+leaf+diagnosis;Firebase+cloud+sync+%7C+Solar-powered+%7C+Farmer-first+design" alt="Typing SVG" />

<br/>

![IoT](https://img.shields.io/badge/IoT-Smart%20Agriculture-2EE6A6?style=for-the-badge&logo=internetarchive&logoColor=white)
![ESP8266](https://img.shields.io/badge/ESP8266-Microcontroller-E7352C?style=for-the-badge&logo=espressif&logoColor=white)
![Firebase](https://img.shields.io/badge/Firebase-Realtime%20DB-FFCA28?style=for-the-badge&logo=firebase&logoColor=black)
![Python](https://img.shields.io/badge/Python-Flask%20Backend-3776AB?style=for-the-badge&logo=python&logoColor=white)
![AI](https://img.shields.io/badge/AI-Leaf%20Analyzer-8A2BE2?style=for-the-badge&logo=tensorflow&logoColor=white)

<br/>

![Stars](https://img.shields.io/github/stars/IamSristi/AgriNova?style=social)
![Forks](https://img.shields.io/github/forks/IamSristi/AgriNova?style=social)
![Repo Size](https://img.shields.io/github/repo-size/IamSristi/AgriNova?color=2EE6A6)
![Last Commit](https://img.shields.io/github/last-commit/IamSristi/AgriNova?color=2EE6A6)

</div>

<br/>

![divider](https://user-images.githubusercontent.com/74038190/212284100-561aa473-3905-4a80-b561-0d28506553ee.gif)

## 🌾 About the Project

**AgroSmart (a.k.a. AgriNova)** is an end-to-end IoT-based smart agriculture system that fuses **embedded sensing**, **cloud connectivity**, and **artificial intelligence** into one integrated platform. Instead of forcing farmers to juggle separate tools for irrigation timers, soil sensors, and disease guides, AgriNova brings **live sensor monitoring, automated irrigation, AI-powered plant-disease diagnosis, and a conversational farming assistant** together in a single dashboard.

> 💡 Most existing smart-farming tools solve *one* problem — a moisture sensor here, a timer there. AgriNova was designed to close that gap with a single, affordable, farmer-friendly system that also works when the internet doesn't.

<br/>

## ✨ Key Features

<table>
<tr>
<td width="50%" valign="top">

### 📡 Real-Time Sensing
- Live soil moisture, temperature, humidity & water-level readings
- ESP8266-based sensor nodes streaming to Firebase Realtime Database
- Local + Cloud hybrid mode toggle for low-connectivity areas

### 💧 Smart Irrigation
- Automated dual-pump system (tank fill + drain)
- Ultrasonic tank-level sensing with calibratable empty height
- Environment-aware control to cut water wastage

</td>
<td width="50%" valign="top">

### 🧠 AI Leaf Analyzer
- Upload/scan a leaf image for instant disease detection
- Pathological cause breakdown + organic/chemical treatment plan
- Prevention & cultural-practice recommendations
- Exportable PDF diagnostic sheet

### 💬 Kalpataru — the AI Farming Assistant
- Chat-based crop guidance in natural language
- Voice input support
- Multi-language ready

</td>
</tr>
</table>

### 🌞 Sustainability Built-In
- Solar-powered hardware to cut electricity dependency
- Rainwater harvesting integration
- Designed for small & marginal farmers, not just large agribusiness

<br/>

![divider](https://user-images.githubusercontent.com/74038190/212284100-561aa473-3905-4a80-b561-0d28506553ee.gif)

## 📸 App Preview

<div align="center">

**Farm Dashboard — live sensor telemetry, tank status & Kalpataru AI assistant**

<img src="dashboard.png" width="850" alt="AgriNova Farm Dashboard"/>

<br/><br/>

**Leaf Analyzer — AI-driven disease detection with treatment guidance**

<img src="leaf-analyzer.png" width="850" alt="AgriNova Leaf Analyzer"/>

</div>

<br/>

![divider](https://user-images.githubusercontent.com/74038190/212284100-561aa473-3905-4a80-b561-0d28506553ee.gif)

## 🏗️ System Architecture

```mermaid
flowchart LR
    subgraph Field["🌱 Field Hardware"]
        A[Soil Moisture Sensor]
        B[Ultrasonic Sensor]
        C[Humidity / Temperature Sensor]
    end

    subgraph Node["ESP8266 Sensor Node"]
        D[ESP8266 Microcontroller]
        E[Power Supply]
        F[Motor Driver]
    end

    subgraph Actuation["💧 Actuation"]
        G[Pump 1 - Tank Fill]
        H[Pump 2 - Drain Water]
    end

    subgraph Cloud["☁️ Cloud & App"]
        I[(Firebase Realtime DB)]
        J[AgriNova Web App]
        K[Kalpataru AI Assistant]
        L[AI Leaf Analyzer]
    end

    A --> D
    B --> D
    C --> D
    E --> D
    D <--> I
    I <--> J
    D --> F
    F --> G
    F --> H
    J --> K
    J --> L
```

<br/>

## 🧰 Tech Stack

<div align="center">

| Layer | Technologies |
|---|---|
| **Hardware / Firmware** | ESP8266, Soil Moisture / Ultrasonic / DHT Sensors, Motor Driver, Solar Power Module |
| **Connectivity** | Wi-Fi (STA/Local mode), Firebase Realtime Database, hybrid Wi-Fi + LoRa (planned) |
| **Backend** | Python, Flask (`app.py`), `leaf_detector.py` for AI inference |
| **AI / ML** | Leaf disease classification model, chatbot-driven crop guidance (Kalpataru) |
| **Frontend** | Web dashboard (Local/Cloud toggle, live charts, chat widget) |
| **Data / Config** | `crops_requirements.json`, `thresholds.json`, Firebase Realtime DB |

</div>

<br/>

![divider](https://user-images.githubusercontent.com/74038190/212284100-561aa473-3905-4a80-b561-0d28506553ee.gif)

## 🚀 Getting Started

### Prerequisites
- Python 
- A Firebase project (Realtime Database enabled)
- ESP8266 board + supported sensors (for the hardware side)

### Installation

```bash
# 1. Clone the repository
# 2. Install dependencies
# 3. Note: Ollama model files not shared in this repo yet
# 4. Configure environment variables
# 5. Run the app
```

<br/>

## 📂 Project Structure

```
AgriNova/
├── app.py                     # Flask application entry point
├── leaf_detector.py           # AI leaf disease detection logic
├── crops_requirements.json    # Crop-specific requirement data
├── thresholds.json            # Sensor threshold configuration
├── example.json                # Sample data
├── chat-widget-check.js       # Kalpataru chat widget helper
├── requirements.txt           # Python dependencies
├── run_app.bat                # Windows launch script
├── QUICKSTART.md
├── BUGFIX_README.md
└── png files                # App screenshots (dashboard, leaf analyzer)
```

<br/>

![divider](https://user-images.githubusercontent.com/74038190/212284100-561aa473-3905-4a80-b561-0d28506553ee.gif)

## 🔬 Research Foundation

This project builds on a literature review of recent work in IoT-driven precision agriculture, edge-computing for low-latency sensing, and AI/ML-based real-time farm monitoring — and targets the gaps that recur across that body of work:

- ❌ Fragmented single-purpose tools → ✅ one integrated platform
- ❌ Timer-based irrigation → ✅ environment-aware, sensor-driven control
- ❌ Fully internet-dependent systems → ✅ Local/Cloud hybrid mode
- ❌ Expensive precision-ag hardware → ✅ low-cost, open-source stack
- ❌ Lab-only AI models → ✅ a field-usable Leaf Analyzer with real recommendations

<br/>

## 🗺️ Roadmap

- [ ] LoRa-based hybrid communication for zero-connectivity zones
- [ ] Replace ESP8266 with ESP32 to implement ph sensor. (ESP8266 has only one analog pin)
- [ ] Mobile app companion
- [ ] Expanded multi-language support for Kalpataru

<br/>

## 👥 Team
<div align="center">
   Srinjoy Tambuli 
  💠 Sristi Paul
  💠 Shreyasee Biswas  
  💠 Sourik Banerjee  
  💠 Soham Das 
  💠 Suchana Das  

</div>

<div align="center">

### 🌱 Built with care for farmers, by Team Innovengers

<img src="https://user-images.githubusercontent.com/74038190/212284158-e840e285-664b-44d7-b79b-e264b5e54825.gif" width="400">

**⭐ Star this repo if you find it useful!**

</div>
