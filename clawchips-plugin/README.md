# clawchips (OpenClaw plugin)

ClawChips is an OpenClaw plugin that automatically selects local or cloud models for OpenClaw conversations based on the task. It also provides a Dashboard for viewing routing results and memory records.

## ClawChips Installation

### 1. Install OpenClaw

Follow the [OpenClaw official documentation](https://docs.openclaw.ai/install) to install and configure OpenClaw. Skip this step if OpenClaw is already installed.

```bash
npm install -g openclaw@2026.3.24
openclaw onboard --install-daemon
```

Note: ClawChips has been tested with OpenClaw 2026.3.24 (cff6dc9).

### 2. Install ClawChips

#### Get the package

Option 1: Download a release package

Download it from the [release page](https://github.com/airockchip/clawchips/releases).

Option 2: Build the plugin package yourself

Install dependencies before the first build:

```bash
git clone https://github.com/airockchip/clawchips
cd clawchips-plugin/
npm install
```

For later builds, run the following command from the repository root:

```bash
bash scripts/package_dist.sh
```

Copy `dist/clawchips.zip` to the development board for installation.

#### Install the plugin

Run the following command:

```bash
openclaw plugins install clawchips.zip
```

#### Initialize configuration

Run the following command and follow the prompts:

```bash
node ~/.openclaw/extensions/clawchips/scripts/setup.mjs
```

### 3. Restart OpenClaw

```bash
openclaw gateway restart
```

After startup, open the Dashboard Web page at: `http://<ip>:18789/plugins/clawchips/dashboard`

## Usage Guide

### Test routing

After chatting with OpenClaw, open the `Tasks` page in the Dashboard to view routing results.

You can also mark results there. Marked tasks can be viewed on the `Memory` page in the Dashboard.

### Chat directives

If the routing result is not what you expect, add a directive that starts with `@` in the chat message to choose a model or route to a specific tier. The following directives are currently supported.

- `@model(model-id)`

Example:

```text
@model(Qwen3.6-Plus) Write a SKILL that can send and receive emails
```

- `@local` / `@cloud`

Examples:

```text
@local Hello
```

```text
@cloud Write a SKILL that can send and receive emails
```

Directive settings are remembered and will affect future selections. You can view them on the `Memory` page in the Dashboard.
