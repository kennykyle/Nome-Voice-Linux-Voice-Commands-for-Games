# Echo Cancelling

When you play with speakers, the microphone also hears the game, videos, and voice
chat, and those sounds can trigger commands. An echo-cancelled microphone removes
whatever your speakers are playing before Nome Voice hears it.

```bash
bash scripts/setup-echo-cancel.sh
```

Then choose **Microphone, echo cancelled (Nome Voice)** in Nome Voice > Settings > Setup >
Microphone.

How it works:

- It uses PipeWire's WebRTC echo canceller, which also reduces steady background noise.
- It follows your default microphone and default output, so it keeps working when you
  switch between speakers and a headset. With a headset there is little to remove, and
  it simply passes your voice through.
- It runs as its own small PipeWire client (`nome-voice-echo-cancel.service`), so your
  normal audio is never restarted. It only uses the microphone while Nome Voice is
  listening, and other apps keep using the regular microphone.

To remove it:

```bash
bash scripts/setup-echo-cancel.sh --remove
```
