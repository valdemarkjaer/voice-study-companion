const UTTERANCE_POLICIES = new Set(["locked", "open_barge_in"]);

function requirePositiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new RangeError(`${name} must be a positive integer`);
  }
}

function requireUtteranceId(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new TypeError("utteranceId must be a non-empty string");
  }
  return value;
}

/**
 * Joins producer completion to physical queue drain without using elapsed time.
 *
 * A drain only describes the last audio sequence observed by the worklet. A
 * later chunk invalidates that drain, which makes transient streaming
 * underflow harmless. All methods ignore events for stale generations or
 * utterances; protocol-order errors in the active utterance fail closed.
 */
export class PlaybackHandshake {
  constructor(generationId = 1) {
    requirePositiveInteger(generationId, "generationId");
    this.generationId = generationId;
    this.active = null;
    this.seenUtteranceIds = new Set();
  }

  setGeneration(generationId) {
    requirePositiveInteger(generationId, "generationId");
    if (generationId < this.generationId) {
      return false;
    }
    if (generationId > this.generationId) {
      this.generationId = generationId;
      this.active = null;
      this.seenUtteranceIds.clear();
    }
    return true;
  }

  begin({ generationId, utteranceId, utterancePolicy }) {
    requirePositiveInteger(generationId, "generationId");
    requireUtteranceId(utteranceId);
    if (!UTTERANCE_POLICIES.has(utterancePolicy)) {
      throw new RangeError("unsupported utterance policy");
    }
    if (generationId !== this.generationId) {
      return false;
    }
    if (this.seenUtteranceIds.has(utteranceId)) {
      return Boolean(
        this.active &&
          !this.active.acknowledged &&
          this.active.utteranceId === utteranceId &&
          this.active.utterancePolicy === utterancePolicy,
      );
    }
    if (this.active && !this.active.acknowledged) {
      if (
        this.active.generationId === generationId &&
        this.active.utteranceId === utteranceId &&
        this.active.utterancePolicy === utterancePolicy
      ) {
        return true;
      }
      throw new Error("playback already active");
    }
    this.active = {
      generationId,
      utteranceId,
      utterancePolicy,
      lastAudioSequence: 0,
      drainedAudioSequence: 0,
      producerEnded: false,
      acknowledged: false,
    };
    this.seenUtteranceIds.add(utteranceId);
    return true;
  }

  noteAudio({ generationId, utteranceId, audioSequence }) {
    requirePositiveInteger(audioSequence, "audioSequence");
    if (!this.matches(generationId, utteranceId)) {
      return false;
    }
    if (this.active.producerEnded || this.active.acknowledged) {
      throw new Error("playback audio arrived after producer end");
    }
    if (audioSequence <= this.active.lastAudioSequence) {
      return false;
    }
    if (audioSequence !== this.active.lastAudioSequence + 1) {
      throw new Error("playback audio arrived out of order");
    }
    this.active.lastAudioSequence = audioSequence;
    this.active.drainedAudioSequence = 0;
    return true;
  }

  producerEnded({ generationId, utteranceId }) {
    if (!this.matches(generationId, utteranceId)) {
      return null;
    }
    this.active.producerEnded = true;
    return this.acknowledgement();
  }

  locallyDrained({ generationId, utteranceId, audioSequence }) {
    if (!this.matches(generationId, utteranceId)) {
      return null;
    }
    if (audioSequence !== this.active.lastAudioSequence || audioSequence < 1) {
      return null;
    }
    this.active.drainedAudioSequence = audioSequence;
    return this.acknowledgement();
  }

  acknowledgement() {
    const current = this.active;
    if (
      !current ||
      current.acknowledged ||
      !current.producerEnded ||
      current.lastAudioSequence < 1 ||
      current.drainedAudioSequence !== current.lastAudioSequence
    ) {
      return null;
    }
    return {
      generationId: current.generationId,
      utteranceId: current.utteranceId,
      utterancePolicy: current.utterancePolicy,
    };
  }

  markAcknowledged({ generationId, utteranceId }) {
    const ready = this.acknowledgement();
    if (
      !ready ||
      ready.generationId !== generationId ||
      ready.utteranceId !== utteranceId
    ) {
      return false;
    }
    this.active.acknowledged = true;
    return true;
  }

  matches(generationId, utteranceId) {
    return Boolean(
      this.active &&
        generationId === this.generationId &&
        this.active.generationId === generationId &&
        this.active.utteranceId === utteranceId,
    );
  }
}
