# Test fixtures

`speech-5s.wav` — two pangrams synthesized with `espeak-ng` (en-us, 150 wpm),
resampled to 16 kHz mono. Machine-generated, so no third-party rights apply.
Regenerate with:

```sh
espeak-ng -v en-us -s 150 "the quick brown fox jumps over the lazy dog. pack my box with five dozen liquor jugs." -w /tmp/tts.wav
ffmpeg -y -i /tmp/tts.wav -ar 16000 -ac 1 speech-5s.wav
```

`speech-5s.txt` holds the transcript, one alignment "line" per row. The pair
drives `kashi_server.worker.warmup`, the smoke gate for the whole
torch + ctc-forced-aligner stack (CI slow job, image build, worker startup).
