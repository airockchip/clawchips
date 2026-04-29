<div align="center">
  <img src="res/logo.png" width="300" alt="ClawChips logo" />

  <h1 align="center"><strong style="color:rgb(202, 31, 31);">ClawChips</strong></h1>

**ClawChips**: An open source solution for deploying and optimizing OpenClaw on edge devices

**English | [中文](./README_ZH.md)**

</div>

---

**Note**: The current release is a technical preview and is still being optimized and iterated.

## About ClawChips

ClawChips is an open source reference solution for deploying and optimizing OpenClaw on edge devices. It provides a series of edge-side algorithm SKILLs; ModelHub, an edge algorithm scheduling service; an intelligent edge-cloud routing gateway; a visual Dashboard; and more to improve the experience of using OpenClaw on edge platforms.

| Feature | Description |
| --- | --- |
| **SKILLS** | A set of built-in edge algorithm SKILLs delivering localized speech, vision, knowledge-base, and related capabilities. |
| **ModelHub** | A gateway for managing and scheduling algorithm models so SKILLs can easily connect to algorithm services. |
| **Edge-Cloud Intelligent Routing (Experimental)** | Intelligently identifies task complexity and automatically dispatches requests between local and cloud models to save token usage; supports memory-based routing to continuously improve routing decisions. |
| **Dashboard** | Provides routing statistics, runtime configuration, feedback labeling, and memory inspection. |

---

## SKILLS

The [skills](./skills/README.md) directory includes a series of SKILLs for edge chip platform capabilities; they are continuously being adapted, expanded, and refined. Developer contributions are welcome.

The following Skill application examples cover ASR (speech recognition), TTS (speech synthesis), and VLM (vision-language model) algorithm services deployed via ModelHub, and can be used as references for further development.

| Skill | Directory | Function | Typical Use |
|---|---|---|---|
| `rk-asr` | `rk-asr/` | Transcribes audio files to text. | Speech-to-text, audio transcription, or retrieving speech recognition results. |
| `rk-tts` | `rk-tts/` | Converts text to audio. | Text-to-speech, audio generation, or reading text aloud on the device. |
| `rk-vl` | `rk-vl/` | Performs natural-language target detection on images using a VLM, with support for continuous camera monitoring and alerts. | Detecting and monitoring naturally described targets such as parcels and deliveries, or falls by seniors, from images or cameras. |
| `rk-rag` | `rk-rag/` | Builds and queries a knowledge base. | Importing documents into a local vector database and performing retrieval QA on specified knowledge bases. |
| `rk-meeting-watcher` | `rk-meeting-watcher/` | Listens to meeting audio in real time, transcribes it with ASR, matches configured keywords, and sends alerts when keywords are hit. | Monitoring important meeting keywords and receiving timely notifications. |

## ModelHub

ModelHub is ClawChips' local model service scheduling gateway for RK edge chips. It is used to manage algorithm model services available on the board and provide a stable invocation entry point for SKILLs, OpenClaw plugins, and other local applications.

It sits between application logic and concrete model services, handling task queues, model service startup, health checks, request forwarding, and result queries based on device resources and service status. Upper-layer SKILLs do not need to care whether a model has started, whether the device is busy, or how service ports are allocated. They only need to submit tasks through a unified API.

ModelHub supports:

- Describing RK3588, RK1820/RK1828, and other devices and their model services with YAML
- Scheduling tasks based on device concurrency and model VRAM / memory usage to avoid resource contention from multiple heavy models
- Automatically running model service start, stop, and health-check commands
- Forwarding HTTP requests to target model services, compatible with OpenAI-style local model APIs

A typical invocation chain is shown below:

```text
┌─────────────────────┐     ┌─────────────────────┐
│        SKILL        │     │   OpenClaw Plugin   │
└──────────┬──────────┘     └──────────┬──────────┘
           │                           │
           └─────────────┬─────────────┘
                         │
                         ▼
                ┌─────────────────┐
                │    ModelHub     │
                │ Scheduler / API │
                └────────┬────────┘
                         │
       ┌─────────────────┼─────────────────┬─────────────────┐
       │                 │                 │                 │
       ▼                 ▼                 ▼                 ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│     ASR     │   │     TTS     │   │     VLM     │   │  Embedding  │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
```

**Notes**

- For more installation, configuration, and client invocation examples, see [model_hub_py](./model_hub_py/README.md).
- Built-in ASR, TTS, VLM, and related algorithm models are included in ClawChips firmware; see [ClawChips_Quick_Start](./ClawChips_Quick_Start.md) for environment setup.

## Edge-Cloud Routing (Experimental)

The ClawChips plugin includes a locally running intelligent routing gateway that sits between OpenClaw and multiple model backends.

- Intelligently detects task complexity and routes task requests between local and cloud via the local intelligent router
- Supports memory-assisted routing for continuously improved routing decisions
- Includes a dashboard configuration interface

```text
┌─────────────────┐     ┌──────────────────────┐               ┌─────────────────────────┐
│    Channels     │────▶│       OpenClaw       │──────────────▶│          CLOUD          │
│                 │     │     Gateway/App      │────────┐      │ ┌─────────────────────┐ │
└─────────────────┘     └──────────────────────┘        │      │ │        Qwen         │ │
                               │      ▲                 │      │ └─────────────────────┘ │
                               │      │                 │      │ ┌─────────────────────┐ │
                               │      │                 │      │ │        Kimi         │ │
                               ▼      │                 │      │ └─────────────────────┘ │
                        ┌─────────────────────┐         │      │ ┌─────────────────────┐ │
                        │      ClawChips      │         │      │ │         GLM         │ │
                        │     LocalRouter     │         │      │ └─────────────────────┘ │
                        └─────────────────────┘         │      │ ┌─────────────────────┐ │
                                                        │      │ │    OpenAI / More    │ │
                                                        │      │ └─────────────────────┘ │
                                                        │      └─────────────────────────┘
                                                        │
                                                        │      ┌─────────────────────────┐
                                                        └─────▶│          LOCAL          │
                                                               │      RKLLM Server       │
                                                               └─────────────────────────┘
```

For more installation, configuration, and usage examples, see [clawchips-plugin](./clawchips-plugin/README.md).

**Note**: Using a local LLM as an OpenClaw provider is still experimental and intended for evaluation only.

---

## Dashboard Features

The gateway provides a Dashboard for convenient backend configuration and statistics viewing. A demo is shown below:

![Dashboard Preview](res/dashboard.gif)

---

## Installation Guide

### Requirements

- RK3588+RK1828 Debian/Ubuntu operating system

### Quick Start

For the full development board environment setup and installation process, see *[ClawChips_Quick_Start](./ClawChips_Quick_Start.md)*.

## Join us, developers: explore countless possibilities together

To fully support efficient development and innovation, we offer a dedicated co-creation support program. Scan the QR code below to apply for complimentary loan of an RK3588 + RK1828 development kit. Based on your submission quality and fit, we will offer enterprises and developers a one-month hands-on kit experience so you can more easily explore the full capabilities of ClawChips and refine high-quality skills.

![Registration QR code](res/baoming.png)

## Reference Projects

- [OpenClaw](https://github.com/openclaw/openclaw)
- [EdgeClaw](https://github.com/OpenBMB/EdgeClaw)
- [UncommonRoute](https://github.com/CommonstackAI/UncommonRoute)
- [ClawRouter](https://github.com/BlockRunAI/ClawRouter)
- [LLMRouter](https://github.com/ulab-uiuc/LLMRouter)

---

## License

MIT
