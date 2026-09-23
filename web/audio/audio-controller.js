const AUDIO_CONSTRAINTS = Object.freeze({
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
});

const DEFAULT_CAPTURE_WORKLET_URL = new URL(
  "./audio-capture-worklet.js",
  import.meta.url,
).href;
const DEFAULT_PLAYER_WORKLET_URL = new URL(
  "./pcm-player-worklet.js",
  import.meta.url,
).href;

export class VoiceStudyAudioController {
  constructor({
    onAudioFrame,
    onPlaybackState,
    onStatus,
    captureWorkletUrl = DEFAULT_CAPTURE_WORKLET_URL,
    playerWorkletUrl = DEFAULT_PLAYER_WORKLET_URL,
  } = {}) {
    this.onAudioFrame = onAudioFrame || (() => {});
    this.onPlaybackState = onPlaybackState || (() => {});
    this.onStatus = onStatus || (() => {});
    this.captureWorkletUrl = String(captureWorkletUrl);
    this.playerWorkletUrl = String(playerWorkletUrl);
    this.context = null;
    this.stream = null;
    this.captureNode = null;
    this.playerNode = null;
    this.silentGain = null;
    this.generationId = 1;
    this.captureMode = "disabled";
    this.flushResolvers = new Map();
    this.nextFlushRequestId = 1;
  }

  async start(generationId = 1) {
    if (this.context) {
      return this.settings();
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
      throw new Error("audio_worklet_unavailable");
    }
    this.generationId = generationId;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: AUDIO_CONSTRAINTS,
      video: false,
    });
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    this.context = new AudioContextConstructor({ latencyHint: "interactive" });
    await Promise.all([
      this.context.audioWorklet.addModule(this.captureWorkletUrl),
      this.context.audioWorklet.addModule(this.playerWorkletUrl),
    ]);
    await this.context.resume();

    const source = this.context.createMediaStreamSource(this.stream);
    this.captureNode = new AudioWorkletNode(
      this.context,
      "voice-study-audio-capture",
      { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] },
    );
    this.silentGain = this.context.createGain();
    this.silentGain.gain.value = 0;
    source.connect(this.captureNode);
    this.captureNode.connect(this.silentGain).connect(this.context.destination);
    this.captureNode.port.onmessage = (event) => {
      if (
        event.data?.type === "audio" &&
        event.data.generationId === this.generationId &&
        this.captureMode === "capturing"
      ) {
        this.onAudioFrame(event.data.pcm, event.data.generationId);
      } else if (event.data?.type === "flushed") {
        const resolve = this.flushResolvers.get(event.data.requestId);
        if (resolve) {
          this.flushResolvers.delete(event.data.requestId);
          resolve();
        }
      }
    };

    this.playerNode = new AudioWorkletNode(this.context, "voice-study-pcm-player", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.playerNode.connect(this.context.destination);
    this.playerNode.port.onmessage = (event) => {
      if (["drained", "cancelled", "error"].includes(event.data?.type)) {
        this.onPlaybackState(event.data);
      }
    };
    const accepted = this.settings();
    this.onStatus(accepted);
    return accepted;
  }

  settings() {
    const track = this.stream?.getAudioTracks()[0];
    const accepted = track?.getSettings?.() || {};
    return {
      requested: { ...AUDIO_CONSTRAINTS },
      accepted: {
        channelCount: accepted.channelCount ?? null,
        echoCancellation: accepted.echoCancellation ?? null,
        noiseSuppression: accepted.noiseSuppression ?? null,
        autoGainControl: accepted.autoGainControl ?? null,
        sampleRate: accepted.sampleRate ?? this.context?.sampleRate ?? null,
      },
    };
  }

  setCaptureMode(mode) {
    if (!["disabled", "armed", "capturing"].includes(mode)) {
      throw new RangeError("invalid capture mode");
    }
    this.captureMode = mode;
    this.captureNode?.port.postMessage({
      type: "configure",
      mode,
      generationId: this.generationId,
    });
  }

  stopCapture({ nextMode = "armed" } = {}) {
    if (!this.captureNode || this.captureMode !== "capturing") {
      this.setCaptureMode(nextMode);
      return Promise.resolve();
    }
    const requestId = this.nextFlushRequestId;
    this.nextFlushRequestId += 1;
    this.captureMode = nextMode;
    const complete = new Promise((resolve) => {
      this.flushResolvers.set(requestId, resolve);
    });
    this.captureNode.port.postMessage({ type: "flush", requestId, nextMode });
    return complete;
  }

  beginPlayback(generationId, utteranceId) {
    if (!this.playerNode) {
      throw new Error("audio must be started");
    }
    this.playerNode.port.postMessage({
      type: "start",
      generationId,
      utteranceId,
    });
  }

  enqueue(
    pcm,
    generationId = this.generationId,
    utteranceId,
    audioSequence,
  ) {
    if (!(pcm instanceof ArrayBuffer) || !this.playerNode) {
      throw new TypeError("pcm must be an ArrayBuffer and audio must be started");
    }
    this.playerNode.port.postMessage(
      { type: "enqueue", generationId, utteranceId, audioSequence, pcm },
      [pcm],
    );
  }

  cancelGeneration(nextGenerationId) {
    if (nextGenerationId !== this.generationId + 1) {
      throw new RangeError("generation must advance contiguously");
    }
    const previous = this.generationId;
    this.generationId = nextGenerationId;
    this.captureMode = "disabled";
    this.captureNode?.port.postMessage({
      type: "configure",
      mode: "disabled",
      generationId: nextGenerationId,
    });
    this.playerNode?.port.postMessage({
      type: "cancel",
      generationId: previous,
      nextGenerationId,
    });
  }

  async stop() {
    this.captureMode = "disabled";
    for (const resolve of this.flushResolvers.values()) {
      resolve();
    }
    this.flushResolvers.clear();
    for (const track of this.stream?.getTracks() || []) {
      track.stop();
    }
    this.captureNode?.disconnect();
    this.playerNode?.disconnect();
    this.silentGain?.disconnect();
    if (this.context && this.context.state !== "closed") {
      await this.context.close();
    }
    this.context = null;
    this.stream = null;
    this.captureNode = null;
    this.playerNode = null;
    this.silentGain = null;
  }
}

export function createAudioController(options) {
  return new VoiceStudyAudioController(options);
}
