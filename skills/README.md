# Skills Directory

## Algorithm Capability Skills


| Skill | Directory | Capabilities | Typical Use |
|---|---|---|---|
| `rk-asr` | `rk-asr/` | Transcribes audio files to text. | Speech-to-text, audio transcription, or obtaining speech recognition results. |
| `rk-tts` | `rk-tts/` | Converts text to audio. | Text-to-speech, audio generation, or reading text aloud on device. |
| `rk-vl` | `rk-vl/` | Natural-language target detection and recognition on images using a VLM; supports continuous camera monitoring and alerts. | Detecting and monitoring natural-language targets such as package deliveries or elderly falls from images or a camera. |
| `rk-rag` | `rk-rag/` | Builds and queries a knowledge base. | Importing documents into a local vector store and retrieval-based Q&A against a chosen knowledge base. |
| `rk-meeting-watcher` | `rk-meeting-watcher/` | Listens to meeting audio in real time, transcribes with ASR, matches configured keywords, and sends alerts when matched. | Monitoring meetings for important keywords and receiving timely reminders. |


**Note**: The required algorithm services are built into ClawChips firmware. For environment setup, see [ClawChips_Quick_Start](../ClawChips_Quick_Start.md).

## Other Skills


| Skill | Directory | Capabilities | Typical Use |
|---|---|---|---|
| `rk-adb` | `rk-adb/` | Connects to Rockchip Android devices over local-network ADB or remote USB-over-SSH workflows. | `adb shell`, `push/pull`, `logcat`, `setprop`, `reboot`, and device inspection tasks. |
| `rk-model-benchmark` | `rk-model-benchmark/` | Benchmarks LLM and CNN models on Rockchip NPU devices such as RK1820 and RK1828. | Inference performance testing, FPS checks, latency measurement, throughput analysis, and NPU frequency experiments. |
| `rk-hwc-troubleshooting` | `rk-hwc-troubleshooting/` | Diagnoses Rockchip Android HWC and display pipeline issues using reference-driven troubleshooting steps. | Black screens, flicker, display anomalies, dropped frames, crashes, and other graphics or display issues. |
| `rk-binary-image-decoder` | `rk-binary-image-decoder/` | Parses raw binary image buffers from filename parameters and decodes them into viewable image files. | Turning binary dumps into images for inspection or sharing. |
| `rk-remind` | `rk-remind/` | Creates scheduled reminders and sends reminder messages via QQ when they fire. | One-time or recurring reminders created from QQ chat commands. |

