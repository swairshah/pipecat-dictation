# Pipecat Dictation

Voice dictation assistant that uses an LLM to turn speech into text and send that text to target windows.

This is a slightly cleaned up version of code I use every day and am always hacking on. I stripped out a lot of stuff (screenshot and image pasting, command sequences, todo list memos) to make this more approachable, but if there's interest in maintaining and extending this, we can add features back in!

Current features:
  - keep track of target windows by name
  - dictate in "immediate mode" or "accumulate mode"
  - clean up text before sending to target window

Window name/position mappings are persisted in ~/.pipecat-dictation/window_memory.json

## Run this thing

(See platform specific notes, below, too ...)

```
export OPENAI_API_KEY=...
uv run bot-realtime-api.py
```

Then open `http://localhost:7860` in your browser to connect to the bot.

Try saying:
- "Use the current window as the target window and name it terminal"
- "Let's test, send hello world to the terminal"

## How It Works

The bot is a [Pipecat](https://pipecat.ai) voice agent that uses OpenAI's Realtime API and a few window management tools. You can use other models and different pipeline designs if you want to! Smaller/older models require different prompting and, in general, can't handle as much ambiguity in conversation flow and instructions.

We run the bot process locally and connect to it via a [serverless WebRTC](https://docs.pipecat.ai/server/services/transport/small-webrtc) connection. We use WebRTC for flexibility and because Pipecat comes with a bunch of helpful [client-side SDK tooling](https://github.com/pipecat-ai/voice-ui-kit). (For example, we get echo cancellation and a simple developer playground UI by using the pipecat-ai-small-webrtc-prebuilt Python package and connecting via the browser.)

The bot loads instructions from [prompt-realtime-api.txt](./prompt-realtime-api.txt)

## Platform specific notes

### macOS

Python must have Accessibility/Input Monitoring permissions to send keystrokes (System Settings → Privacy & Security → Accessibility and Input Monitoring).

### Linux

We use `ydotool` to send keystrokes. The venerable `xdotool` no longer works on modern systems that use Wayland.

On Ubuntu:

```bash
sudo apt install ydotool
sudo usermod -a -G input $USER
# log out/in for group change to apply or run `newgrp input`
```

Note that we are using an old version of ydotool because that's what you can install via apt. If you're on a distro with a newer ydotool or you've built ydotool from source, the arguments to `ydotool` will be incompatible. PRs are welcome!
