#!/usr/bin/env python3
"""Generate a μ-law 8kHz mono clip for the Telnyx stress test.

Uses the macOS `say` command (free, local) to synthesize speech, then converts
it to 8kHz mono PCMU (μ-law) — the exact wire format Telnyx streams and the bot's
TelnyxFrameSerializer decodes (inbound_encoding/outbound_encoding = PCMU @ 8kHz).

Output: loadtest/sample_ulaw_8k.raw  (raw 8-bit μ-law, 8000 Hz, mono)
"""
import argparse
import audioop
import os
import subprocess
import sys
import tempfile
import wave

DEFAULT_TEXT = "Hi, I would like to know my bin collection day for 50 Vine Street Hurstville. Thank you."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=DEFAULT_TEXT, help="Utterance to synthesize")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "sample_ulaw_8k.raw"))
    ap.add_argument("--voice", default=None, help="Optional macOS voice name (e.g. Karen, Daniel)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        wav_path = os.path.join(td, "say.wav")
        cmd = ["say", "--data-format=LEI16@8000", "--file-format=WAVE", "-o", wav_path]
        if args.voice:
            cmd += ["-v", args.voice]
        cmd.append(args.text)
        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            sys.exit(f"`say` failed ({e}). This generator requires macOS.")

        with wave.open(wav_path, "rb") as wf:
            nch, sw, fr, nframes = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
            pcm = wf.readframes(nframes)

        if nch == 2:
            pcm = audioop.tomono(pcm, sw, 0.5, 0.5)
        if sw != 2:
            pcm = audioop.lin2lin(pcm, sw, 2)
            sw = 2
        if fr != 8000:
            pcm, _ = audioop.ratecv(pcm, sw, 1, fr, 8000, None)

        ulaw = audioop.lin2ulaw(pcm, 2)

    with open(args.out, "wb") as f:
        f.write(ulaw)

    secs = len(ulaw) / 8000.0
    print(f"✓ wrote {args.out}  ({len(ulaw)} bytes μ-law, {secs:.1f}s @ 8kHz mono)")


if __name__ == "__main__":
    main()
