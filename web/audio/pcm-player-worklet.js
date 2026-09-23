import { GenerationPcmPlayback } from "./pcm-ring-buffer.mjs";

class VoiceStudyPcmPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.playback = new GenerationPcmPlayback({
      capacitySamples: sampleRate * 60,
      generationId: 1,
      inputSampleRate: 24_000,
      outputSampleRate: sampleRate,
    });
    this.port.onmessage = (event) => {
      try {
        if (event.data?.type === "start") {
          this.playback.begin(
            event.data.generationId,
            event.data.utteranceId,
          );
        } else if (event.data?.type === "enqueue") {
          this.playback.enqueue(
            event.data.generationId,
            event.data.utteranceId,
            event.data.audioSequence,
            new Int16Array(event.data.pcm),
          );
        } else if (event.data?.type === "cancel") {
          const cancelled = this.playback.cancel(
            event.data.generationId,
            event.data.nextGenerationId,
          );
          if (cancelled) {
            this.port.postMessage({
              type: "cancelled",
              generationId: event.data.nextGenerationId,
            });
          }
        }
      } catch (error) {
        this.port.postMessage({
          type: "error",
          code: "pcm_queue_error",
          message: error instanceof Error ? error.message : "PCM queue error",
        });
      }
    };
  }

  process(_inputs, outputs) {
    const channel = outputs[0]?.[0];
    if (!channel) {
      return true;
    }
    const { pcm, event } = this.playback.pull(channel.length);
    channel.set(pcm);
    if (event) {
      this.port.postMessage(event);
    }
    return true;
  }
}

registerProcessor("voice-study-pcm-player", VoiceStudyPcmPlayerProcessor);
