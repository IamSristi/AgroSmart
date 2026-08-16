"""
AgriNova — Leaf Disease Spot Detector & Visual Marking Engine
============================================================
Detects, marks, and quantifies affected spots, lesions, chlorosis, and necrosis
on plant leaves using Computer Vision (OpenCV, LAB/HSV color segmentation,
and contour analysis).
"""

import cv2
import numpy as np
import base64
import io
from PIL import Image

def detect_and_mark_leaf_spots(image_path_or_bytes, min_spot_area=20, max_spots=150):
    """
    Analyzes a leaf image to detect affected spots/lesions and generates
    annotated images, heatmaps, and structured spot metrics.
    
    Returns:
        dict containing:
            - marked_image_base64: JPEG base64 of annotated image with bounding boxes & contours
            - heatmap_image_base64: JPEG base64 of disease density heatmap
            - mask_image_base64: JPEG base64 of binary affected area mask
            - spot_count: Total number of detected disease spots
            - affected_area_pct: Percentage of leaf area covered by lesions/spots
            - healthy_area_pct: Percentage of leaf area healthy
            - total_leaf_pixels: Area of leaf in pixels
            - spots: List of detected spot metadata [{id, x, y, w, h, cx, cy, area, type, severity}]
            - dominant_symptom: Most frequent lesion type (Necrosis, Chlorosis, Rust, Mildew)
    """
    if isinstance(image_path_or_bytes, str):
        img = cv2.imread(image_path_or_bytes)
    else:
        nparr = np.frombuffer(image_path_or_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        return {"error": "Could not decode leaf image."}

    orig_h, orig_w = img.shape[:2]

    # Resize for consistent processing while preserving aspect ratio
    max_dim = 1000
    if max(orig_h, orig_w) > max_dim:
        scale = max_dim / max(orig_h, orig_w)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0

    h, w = img.shape[:2]

    # 1. Color Space Transformations
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    
    # 2. Leaf Boundary Segmentation (Isolate Leaf from Background)
    # Method A: Excess Green Index (2G - R - B) + Lab Chroma
    b, g, r = cv2.split(img.astype(np.float32))
    exg = 2.0 * g - r - b
    
    # Normalize ExG
    exg_norm = cv2.normalize(exg, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, exg_thresh = cv2.threshold(exg_norm, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Method B: General plant matter in HSV
    plant_hsv = cv2.inRange(hsv, np.array([8, 20, 20]), np.array([110, 255, 255]))
    
    # Combined plant mask
    leaf_mask = cv2.bitwise_or(exg_thresh, plant_hsv)
    
    # Exclude non-leaf background (e.g. stark white background or dark black background)
    non_white = cv2.inRange(hsv, np.array([0, 15, 0]), np.array([180, 255, 245]))
    leaf_mask = cv2.bitwise_and(leaf_mask, non_white)
    
    # Morphological closing to fill holes inside the leaf blade
    k_leaf = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    leaf_mask = cv2.morphologyEx(leaf_mask, cv2.MORPH_CLOSE, k_leaf, iterations=2)
    leaf_mask = cv2.morphologyEx(leaf_mask, cv2.MORPH_OPEN, k_leaf, iterations=1)
    
    # Retain the largest connected components (the leaf/leaves)
    contours_leaf, _ = cv2.findContours(leaf_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours_leaf:
        max_area = max(cv2.contourArea(c) for c in contours_leaf)
        clean_leaf_mask = np.zeros_like(leaf_mask)
        for c in contours_leaf:
            if cv2.contourArea(c) > max_area * 0.1:  # keep primary leaf components
                cv2.drawContours(clean_leaf_mask, [c], -1, 255, -1)
        leaf_mask = clean_leaf_mask

    total_leaf_pixels = int(cv2.countNonZero(leaf_mask))
    if total_leaf_pixels < 200:
        # Fallback if background segmentation is too strict
        leaf_mask = np.ones((h, w), dtype=np.uint8) * 255
        total_leaf_pixels = h * w

    # 3. Detect Healthy Green Leaf Tissue
    healthy_green_mask = cv2.inRange(hsv, np.array([35, 45, 40]), np.array([90, 255, 255]))
    healthy_green_mask = cv2.bitwise_and(healthy_green_mask, leaf_mask)

    # 4. Multi-Symptom Disease Spot Segmentation (Inside Leaf Mask)
    # A: Necrotic Spots (Dark Brown, Black, Gray dead lesions)
    # Brown hues: H: 5-25, Sat: 30-255, Val: 20-180
    brown_mask = cv2.inRange(hsv, np.array([5, 30, 20]), np.array([24, 255, 185]))
    # Dark lesions where luminance is significantly low
    dark_mask = cv2.inRange(l_channel, 0, 75)
    necrotic_mask = cv2.bitwise_or(brown_mask, dark_mask)
    necrotic_mask = cv2.bitwise_and(necrotic_mask, leaf_mask)
    
    # B: Chlorosis / Yellow Spots (Nutrient deficiency, viral yellowing, fungal halos)
    # Yellow hues: H: 24-38, Sat: 40-255, Val: 70-255
    yellow_mask = cv2.inRange(hsv, np.array([24, 40, 70]), np.array([36, 255, 255]))
    yellow_mask = cv2.bitwise_and(yellow_mask, leaf_mask)

    # C: Rust / Fungal Pustules (Orange / Reddish-Brown)
    rust_mask = cv2.inRange(hsv, np.array([0, 60, 50]), np.array([12, 255, 255]))
    rust_mask = cv2.bitwise_and(rust_mask, leaf_mask)

    # D: White Powdery / Blight Patches
    # High luminance, low saturation inside leaf
    white_blight_mask = cv2.inRange(hsv, np.array([0, 0, 190]), np.array([180, 45, 255]))
    white_blight_mask = cv2.bitwise_and(white_blight_mask, leaf_mask)

    # Combine all disease spot masks
    combined_spots_mask = cv2.bitwise_or(necrotic_mask, yellow_mask)
    combined_spots_mask = cv2.bitwise_or(combined_spots_mask, rust_mask)
    combined_spots_mask = cv2.bitwise_or(combined_spots_mask, white_blight_mask)
    
    # Exclude healthy green
    combined_spots_mask = cv2.bitwise_and(combined_spots_mask, cv2.bitwise_not(healthy_green_mask))
    combined_spots_mask = cv2.bitwise_and(combined_spots_mask, leaf_mask)

    # Morphological noise filtering on spots
    k_spot = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    combined_spots_mask = cv2.morphologyEx(combined_spots_mask, cv2.MORPH_OPEN, k_spot)
    combined_spots_mask = cv2.morphologyEx(combined_spots_mask, cv2.MORPH_CLOSE, k_spot)

    # 5. Extract Individual Spots & Lesion Contours
    contours, _ = cv2.findContours(combined_spots_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Sort contours by area descending
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    spots = []
    affected_pixels = 0
    type_counts = {"Necrosis": 0, "Chlorosis": 0, "Rust": 0, "Blight": 0}

    # Prepare marked image
    annotated = img.copy()
    overlay = img.copy()
    
    # Visual palette for markings (BGR format)
    COLOR_NECROSIS  = (38, 50, 255)   # Amber-Red / Crimson
    COLOR_CHLOROSIS = (0, 215, 255)   # Gold / Yellow
    COLOR_RUST      = (0, 130, 255)   # Orange
    COLOR_BLIGHT    = (235, 206, 135) # Light Cyan-Blue
    COLOR_ACCENT    = (195, 255, 0)   # AgriNova Teal/Cyan (BGR)

    spot_id = 0
    valid_contours = []

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_spot_area:
            continue
        
        spot_id += 1
        if spot_id > max_spots:
            break

        affected_pixels += int(area)
        valid_contours.append(cnt)

        x, y, sw, sh = cv2.boundingRect(cnt)
        M = cv2.moments(cnt)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
        else:
            cx, cy = x + sw // 2, y + sh // 2

        # Classify spot type based on color inside contour
        spot_crop_hsv = hsv[y:y+sh, x:x+sw]
        spot_mask_crop = np.zeros((sh, sw), dtype=np.uint8)
        shifted_cnt = cnt - np.array([x, y])
        cv2.drawContours(spot_mask_crop, [shifted_cnt], -1, 255, -1)
        
        mean_val = cv2.mean(spot_crop_hsv, mask=spot_mask_crop)
        mean_h, mean_s, mean_v = mean_val[0], mean_val[1], mean_val[2]

        if mean_s < 40 and mean_v > 180:
            spot_type = "Blight / Powdery"
            spot_color = COLOR_BLIGHT
            type_counts["Blight"] += 1
        elif 24 <= mean_h <= 40:
            spot_type = "Chlorosis / Halo"
            spot_color = COLOR_CHLOROSIS
            type_counts["Chlorosis"] += 1
        elif mean_h <= 12 and mean_s > 60:
            spot_type = "Fungal Rust"
            spot_color = COLOR_RUST
            type_counts["Rust"] += 1
        else:
            spot_type = "Necrotic Lesion"
            spot_color = COLOR_NECROSIS
            type_counts["Necrosis"] += 1

        # Determine severity of this specific spot
        if area > 400:
            severity = "Severe"
        elif area > 120:
            severity = "Moderate"
        else:
            severity = "Mild"

        # Record spot details
        spots.append({
            "id": spot_id,
            "x": int(x / scale),
            "y": int(y / scale),
            "width": int(sw / scale),
            "height": int(sh / scale),
            "cx": int(cx / scale),
            "cy": int(cy / scale),
            "area_px": int(area / (scale * scale)),
            "type": spot_type,
            "severity": severity,
            "color_hex": "#{:02x}{:02x}{:02x}".format(spot_color[2], spot_color[1], spot_color[0])
        })

        # 6. Draw Visual Annotations on Marked Image
        # A: Fill the exact lesion contour with translucent color
        cv2.drawContours(overlay, [cnt], -1, spot_color, -1)
        
        # B: Draw bounding box with corner marks
        cv2.rectangle(annotated, (x, y), (x + sw, y + sh), spot_color, 2)
        
        # C: Draw center crosshair/target dot
        cv2.circle(annotated, (cx, cy), 3, (255, 255, 255), -1)
        cv2.circle(annotated, (cx, cy), 5, spot_color, 1)

        # D: Draw high-contrast badge for primary spots (first 30 spots)
        if spot_id <= 30:
            label = f"#{spot_id}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.4
            thickness = 1
            (text_w, text_h), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            
            badge_y = max(y - 5, text_h + 4)
            badge_x = x
            
            # Badge background pill
            cv2.rectangle(annotated, (badge_x, badge_y - text_h - 4), 
                          (badge_x + text_w + 6, badge_y + baseline), (15, 20, 30), -1)
            cv2.rectangle(annotated, (badge_x, badge_y - text_h - 4), 
                          (badge_x + text_w + 6, badge_y + baseline), spot_color, 1)
            # Text
            cv2.putText(annotated, label, (badge_x + 3, badge_y - 2), 
                        font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

    # Blend translucent lesion fill into annotated image
    alpha = 0.42
    cv2.addWeighted(overlay, alpha, annotated, 1 - alpha, 0, annotated)

    # Draw smooth contour outlines on top of the blend
    cv2.drawContours(annotated, valid_contours, -1, (255, 255, 255), 1, cv2.LINE_AA)

    # 7. Generate Disease Heatmap
    # Distance transform / density map of affected areas
    dist_transform = cv2.distanceTransform(combined_spots_mask, cv2.DIST_L2, 5)
    dist_norm = cv2.normalize(dist_transform, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    heatmap_colored = cv2.applyColorMap(dist_norm, cv2.COLORMAP_TURBO)
    
    # Mask heatmap to leaf boundary
    heatmap_final = img.copy()
    heatmap_mask_3ch = cv2.merge([combined_spots_mask, combined_spots_mask, combined_spots_mask])
    
    # Blend heatmap over leaf
    np.copyto(heatmap_final, heatmap_colored, where=(heatmap_mask_3ch > 0))
    cv2.addWeighted(heatmap_final, 0.7, img, 0.3, 0, heatmap_final)

    # 8. Calculate Aggregate Metrics
    affected_pct = round((affected_pixels / max(total_leaf_pixels, 1)) * 100.0, 1)
    affected_pct = min(100.0, affected_pct)
    healthy_pct = max(0.0, round(100.0 - affected_pct, 1))

    dominant_symptom = max(type_counts, key=type_counts.get) if any(type_counts.values()) else "Healthy"
    if spot_id == 0:
        dominant_symptom = "No Spots Detected (Healthy)"

    # 9. Encode Images to Base64
    def to_b64(cv_img):
        _, buffer = cv2.imencode(".jpg", cv_img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("utf-8")

    marked_b64 = to_b64(annotated)
    heatmap_b64 = to_b64(heatmap_final)
    mask_b64 = to_b64(combined_spots_mask)

    return {
        "marked_image": marked_b64,
        "heatmap_image": heatmap_b64,
        "mask_image": mask_b64,
        "spot_count": len(spots),
        "affected_area_pct": affected_pct,
        "healthy_area_pct": healthy_pct,
        "total_leaf_pixels": total_leaf_pixels,
        "spots": spots,
        "dominant_symptom": dominant_symptom,
        "symptom_breakdown": type_counts
    }
