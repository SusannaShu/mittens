# Building Mittens: offline food recognition from smart glasses via iPhone

**A single Swift app on your iPhone can receive camera frames from the ESP32 XIAO Sense over WiFi, classify food in under 10ms via Core ML, and push results to the Vuzix Z100 over BLE — all offline, with ~200–500ms end-to-end latency.** The critical architectural insight is that you should use WiFi (not BLE) for image transfer from the ESP32, the Vuzix Ultralite SDK (not MentraOS cloud) for direct display control, and Apple's FastViT-T8 or a Create ML classifier as your recognition model. This pipeline is well-validated by existing open-source projects like OpenGlass and See-Food, and the phased prototyping path starts with just your iPhone camera before any hardware integration.

---

## The model question is already solved: sub-10ms food classification on iPhone

Core ML inference for image classification on modern iPhones is extraordinarily fast — the bottleneck in your pipeline will be image transfer, not inference. Apple's **FastViT-T8** runs classification in **0.52ms on iPhone 16 Pro** and 0.83ms on iPhone 13 Pro using the Apple Neural Engine, with a model size of just 7.8MB (Float16). MobileNetV2 achieves **1.7ms** on iPhone 12. Even YOLOv8-nano with full object detection (bounding boxes + NMS) clocks in at ~101ms. For a food classifier targeting 100–300 grocery items, you're looking at single-digit millisecond inference.

The practical model choice depends on your prototyping stage. **For the fastest MVP, use Apple's Create ML** directly in Xcode. Create ML uses `VisionFeaturePrint_Screen` as its feature extractor — this model is baked into iOS itself, so the exported `.mlmodel` file contains only the classifier head weights and is measured in **kilobytes, not megabytes**. You drag-and-drop image folders organized by label, training completes in minutes on a Mac, and the output integrates directly into Xcode with zero conversion steps. For 100–300 food classes, this is the most efficient path to a working classifier.

For production-quality accuracy, fine-tune **FastViT-T8** (Apple's own architecture, guaranteed Neural Engine optimization) or **MobileNetV3-Small** via PyTorch, then convert with `coremltools`. Transfer learning on Food-101 shows strong results: EfficientNet-B0 achieves **97.54% top-1** with full fine-tuning, and MobileNetV2 hits **92.5%** — both from pre-trained ImageNet weights. Float16 quantization halves model size with no measurable accuracy loss; INT8 quantization quarters it with 1–2% degradation.

| Model | Inference (iPhone) | Size (F16) | Notes |
|---|---|---|---|
| FastViT-T8 | **0.52–0.83ms** | 7.8MB | Apple-optimized, best ANE perf |
| MobileNetV2 | **1.7ms** | ~7MB | Well-tested, many tutorials |
| MobileNetV3-Small | **~11ms** | ~5MB | Hard-swish causes ANE issues |
| Create ML (VisionFeaturePrint) | **~5–15ms** | **~KB** | Fastest to prototype |
| YOLOv8-nano (detection) | **~101ms** | ~6MB | Only if you need bounding boxes |

One important caveat: MobileNetV3's hard-swish activation is **not fully optimized on the Apple Neural Engine**, a known issue documented in the coremltools repo. FastViT-T8 or MobileNetV2 avoid this problem entirely.

---

## Training a custom grocery classifier with 100–200 images per item

For your 100–300 grocery item classifier, the data strategy combines existing datasets with domain-specific captures. **Fruits-360** provides 141 classes of produce (140K images, MIT license) and is your best starting point for fruits and vegetables. **Freiburg Groceries** adds 25 classes of packaged goods from real store settings. **Food-101** covers 101 prepared dish categories with 750 training images each. Combine these for baseline coverage, then augment with photos captured through the actual smart glasses camera to close the domain gap — Fruits-360's white-background studio shots look nothing like a grocery store aisle through an ESP32 camera.

With transfer learning from ImageNet-pretrained weights, the training data requirements are modest. Research shows **15–30 images per class** achieves ~80% accuracy, **100–200 images per class** hits 85–90%, and **500+ images** pushes past 90–95%. For Mittens' MVP, aim for **100–200 images per target item**, captured in realistic conditions. Data augmentation effectively multiplies your dataset 5–10×: rotation (±15°), brightness/contrast jitter (±40%), horizontal flips, zoom/scale variation (±30%), and Gaussian blur to simulate head movement are all essential. For grocery-specific augmentation, add random specular highlights (simulating packaging reflections under fluorescent lighting) and random erasing/cutout (simulating partial occlusion by hands or other items).

The training pipeline has two clear paths. **Create ML** is ideal for rapid prototyping: drag image folders into Xcode, toggle built-in augmentations (noise, blur, crop, rotation, flip, exposure), train in minutes, get a .mlmodel file. No Python required. The tradeoff is less control over architecture and hyperparameters. **PyTorch + coremltools** gives full control: load a pre-trained MobileNetV3 or FastViT from torchvision, freeze the backbone, replace the final FC layer with `nn.Linear(in_features, num_food_classes)`, train the head for 5–10 epochs, then unfreeze and fine-tune all layers at a lower learning rate (1e-4 to 1e-5) for 10–30 more epochs. Convert with `ct.convert(traced_model, inputs=[ct.ImageType(...)], classifier_config=ct.ClassifierConfig(labels))`. No ONNX intermediate step is needed.

---

## WiFi is the only viable path for ESP32 image transfer

This is the most consequential architectural decision in the pipeline. **BLE cannot stream images fast enough** — real-world ESP32-to-iPhone BLE throughput is **~50–100 KB/s**, meaning a modest 25KB VGA JPEG takes 300–500ms to transfer. WiFi AP mode delivers **2–8 Mbps** (250–1000 KB/s), transferring that same image in **30–100ms**. The choice is unambiguous.

The ESP32 XIAO ESP32S3 Sense creates its own WiFi access point (SoftAP mode) at `192.168.4.1` — your iPhone connects directly to this network with no router needed. The ESP32 runs a lightweight HTTP server or WebSocket server. When triggered (button press, voice command, or periodic timer), it captures a JPEG frame using the OV2640's **hardware JPEG encoder** (zero CPU overhead), and the iPhone fetches it via HTTP GET or receives it over a WebSocket push. The camera supports resolutions from 96×96 up to 1600×1200, with frame rates of 50fps at QVGA and 25fps at VGA.

**Capture at 640×480 (VGA) with JPEG quality 10–15**, producing frames of **25–50KB**. This provides 2.8× more pixels than the 224×224 model input, allowing the iPhone to crop and center the food item. The iPhone's vImage or Core Image framework downscales to 224×224 in under 1ms. Don't capture at model-input resolution directly — the OV2640 doesn't natively support 224×224, and higher-resolution capture gives better auto-exposure and scene context.

| Transfer Method | Throughput (to iPhone) | 25KB JPEG Transfer | Viable for Images? |
|---|---|---|---|
| WiFi AP + WebSocket | **250–1000 KB/s** | **25–100ms** | ✅ Yes |
| WiFi AP + HTTP | **250–1000 KB/s** | **50–150ms** | ✅ Yes |
| BLE 5.0 (2M PHY) | **50–100 KB/s** | **250–500ms** | ❌ Too slow |
| ESP-NOW | N/A | N/A | ❌ iPhone incompatible |
| WiFi Direct | N/A | N/A | ❌ ESP32 doesn't support it |

One practical nuance: iPhones may disconnect from an ESP32 AP that lacks internet access. Mitigate this with iOS `NEHotspotConfiguration` API or a captive portal response. Also, lower the camera XCLK from 20MHz to 8MHz to reduce WiFi interference — a well-documented issue where camera and WiFi share DMA resources on the ESP32.

**Use triggered single captures, not continuous streaming.** For a grocery recognition use case ("point at item, identify it"), triggered capture saves **5–10× battery life** over continuous streaming. The ESP32 can deep-sleep between captures at **63.8μA** (vs. 150–300mA during active WiFi TX). With a 540mAh LiPo battery, expect ~2–3 hours of continuous streaming but **8–12 hours** of on-demand triggered captures. The Owl project (XIAO ESP32S3 wearable) validates these numbers.

---

## Skip MentraOS — use the Vuzix Ultralite SDK directly

This is a critical finding. **MentraOS (formerly AugmentOS) is a cloud-based platform** — your third-party app runs as a server that connects to MentraOS Cloud via WebSocket, which then routes to the phone app, which then forwards to the glasses over BLE. The full path is: `Your Server ↔ MentraOS Cloud ↔ Phone App ↔ Z100 Glasses`. This architecture **requires internet connectivity**, adds cloud latency, limits display output to text-only layouts, and defeats the entire purpose of offline on-device inference.

Instead, use the **Vuzix Ultralite SDK for iOS** — a native Swift framework that communicates **directly via BLE** from your iPhone app to the Z100 glasses with no cloud dependency. The SDK is available via Swift Package Manager (`github.com/Vuzix/UltraliteSDK-releases-iOS`, v1.9.0+) and supports iOS 14+. It provides:

- **Canvas layout** with up to 7 text boxes (positioned with explicit coordinates), 3 foreground images (<100×100 pixels for icons), and a full 640×480 background image
- BLE pairing handled by `UltralitePickerController` (built-in scanner/picker UI)
- Tap event detection on the glasses temple
- Battery monitoring

The Z100 display is **640×480 monochrome green, right-eye only, 30° FOV**. It has no camera, no microphone — it's purely a display and touch sensor. For Mittens' food recognition results, use text boxes showing the food name, calorie count, and key nutrient information. Small foreground icons (<100×100) can indicate nutrient status (e.g., checkmark for adequate, warning for deficient). Avoid sending full-screen background images frequently — BLE bandwidth to the Z100 makes large image transfers slow.

A **single Swift iOS app** can simultaneously maintain a WiFi connection to the ESP32 (via `Network.framework` or `URLSession`), run Core ML inference on received frames, and maintain a separate BLE connection to the Vuzix Z100 via the Ultralite SDK. iOS supports up to 7–8 concurrent BLE connections; you only need one (to the Z100). Swift's modern concurrency model (`async/await`, actors) handles the threading cleanly — camera frames arrive on a background thread, inference runs on the Neural Engine asynchronously, and display updates dispatch to the Ultralite SDK's BLE connection.

---

## End-to-end architecture and the complete nutrition pipeline

The full Mittens pipeline for grocery store use breaks down into six stages with a realistic **200–500ms total latency**:

| Stage | Time | Method |
|---|---|---|
| ESP32 camera capture (VGA JPEG) | 50–170ms | OV2640 hardware JPEG encoder |
| WiFi transfer to iPhone | 30–100ms | HTTP GET or WebSocket on ESP32 AP |
| Image decode + resize to 224×224 | <1ms | vImage/Core Image |
| Core ML food classification | 1–10ms | FastViT-T8 or MobileNetV2 on ANE |
| Nutrition DB lookup | 1–5ms | Local SQLite query |
| BLE text push to Vuzix Z100 | 20–50ms | Ultralite SDK canvas text |
| **Total** | **~103–336ms** | |

For the nutrition lookup, bundle a **curated USDA FoodData Central SQLite subset** (~5–10MB) mapping your model's class labels to USDA `fdcId` values. The full USDA database is ~430MB as SQLite (available at `github.com/MenuLogistics/USDASQLite`), but for 100–300 items you only need a fraction. The data is public domain (CC0). For online use, fall back to the USDA FoodData Central REST API. The nutrient gap analysis pipeline then becomes: identify food → look up nutrients per 100g → estimate portion (either fixed defaults or user-input) → compare against RDA values stored locally → flag deficiencies → format as display text → push to Z100.

**Build the app in Swift/SwiftUI.** This is non-negotiable. Core ML generates native Swift interfaces automatically. The Vuzix Ultralite SDK is a native xcFramework. Core Bluetooth is a native framework. React Native and Flutter would require bridges for all three, adding latency, complexity, and bugs with simultaneous BLE connections. The See-Food project on GitHub demonstrates the complete pattern: CoreML food classification + nutrition info lookup in a native Swift app.

For the home WiFi scenario (detailed meal analysis), your old laptop running **Ollama + Qwen2.5-VL** handles the heavy lifting. The ESP32 can connect to your home WiFi network (STA mode instead of AP mode) and stream higher-resolution images to the laptop for richer analysis. The iPhone app can detect whether it's on the home network (by checking SSID or reachability of the laptop's IP) and route inference accordingly — local Core ML for quick grocery identification on the go, or forward to the laptop for detailed meal analysis at home.

---

## Phased implementation: start with just your iPhone camera

**Phase 1 (Week 1–2): Validate the ML model using only the iPhone camera.** Download the pre-built Food101.mlmodel from Awesome-CoreML-Models (`github.com/likedan/Awesome-CoreML-Models`) and build a minimal SwiftUI app with `VNCoreMLRequest` feeding the live camera. Alternatively, train a custom classifier in Create ML by dragging Fruits-360 image folders into Xcode — this takes minutes. Test classification accuracy and inference speed. Add a local SQLite table mapping food labels to basic nutrition data. This phase requires zero hardware beyond your iPhone and validates the entire ML pipeline.

**Phase 2 (Week 3): Add the Vuzix Z100 display.** Integrate the Ultralite SDK via Swift Package Manager. Use `UltralitePickerController` to pair with the glasses. When the iPhone camera classifies a food item, push the result as a text box to the Z100's canvas. You now have a working demo: point iPhone at food → see name and nutrients on glasses.

**Phase 3 (Week 4–5): Integrate the ESP32 XIAO Sense.** Flash the ESP32 with the standard `CameraWebServer` Arduino example, modified to serve single JPEG snapshots at an HTTP endpoint (`/capture`). Set it to AP mode. In the iOS app, connect to the ESP32's WiFi and fetch frames via `URLSession.shared.data(from: captureURL)`. Feed the JPEG into the existing Core ML pipeline. The r2Cam Swift framework (`github.com/TordWessman/r2Cam`) provides a ready-made TCP streaming client if you prefer raw TCP over HTTP.

**Phase 4 (Week 6–8): Polish and optimize.** Implement a trigger mechanism (button GPIO on ESP32, or tap gesture on Z100 temple sensor forwarded to the app). Add ESP32 deep sleep between captures for battery optimization. Train a custom classifier on your specific target items using photos captured through the ESP32 camera. Build the full nutrient gap analysis pipeline: food ID → USDA lookup → compare against personal RDA targets → format concise display. Add pantry tracking with local persistence (Core Data or SQLite).

**Phase 5 (Ongoing): Expand capabilities.** Add barcode scanning (AVFoundation's built-in barcode detector) for packaged goods. Integrate with the existing Mittens server on Railway for calendar-aware meal suggestions. Build the home-WiFi pathway to Ollama for detailed meal analysis. Consider a custom ESP32 PCB with LiPo connector sized for the glasses frame.

Key reference projects to study before writing any code:

- **OpenGlass** (`github.com/BasedHardware/OpenGlass`) — XIAO ESP32S3 Sense AI glasses using BLE to phone, 4K+ GitHub stars
- **See-Food** (`github.com/chaitanya-ramji/See-Food`) — iOS food classifier with nutrition info, 150+ classes, Core ML
- **r2Cam** (`github.com/TordWessman/r2Cam`) — Swift framework for receiving ESP32-CAM TCP streams on iOS
- **Vuzix Ultralite iOS Sample** (`github.com/Vuzix/ultralite-sdk-ios-sample`) — Official Swift/SwiftUI sample for Z100
- **Food101-CoreML** (`github.com/ph1ps/Food101-CoreML`) — Pre-converted Core ML model ready to drop in

---

## Conclusion

The Mittens smart glasses nutrition pipeline is not only feasible — every component has been validated independently by existing projects. The two non-obvious decisions that simplify everything: **use WiFi AP mode (not BLE) for ESP32-to-iPhone image transfer**, and **use the Vuzix Ultralite SDK (not MentraOS) for direct iPhone-to-Z100 display control**. Together, these choices eliminate the need for cloud connectivity, minimize latency to under 500ms, and allow everything to run in a single native Swift app. Start with your iPhone camera and a Create ML classifier this week — you can have a working food-to-display demo before touching any hardware.