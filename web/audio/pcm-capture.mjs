export class Pcm16Resampler {
  constructor(inputRate, outputRate = 24_000) {
    if (
      !Number.isFinite(inputRate) ||
      !Number.isFinite(outputRate) ||
      inputRate <= 0 ||
      outputRate <= 0 ||
      outputRate > inputRate
    ) {
      throw new RangeError("sample rates must be positive and output <= input");
    }
    this.inputRate = inputRate;
    this.outputRate = outputRate;
    this.ratio = inputRate / outputRate;
    this.tail = null;
    this.position = 0;
  }

  reset() {
    this.tail = null;
    this.position = 0;
  }

  process(input) {
    if (!(input instanceof Float32Array)) {
      throw new TypeError("input must be Float32Array");
    }
    if (input.length === 0) {
      return new Int16Array();
    }
    const source = new Float32Array(input.length + (this.tail === null ? 0 : 1));
    let inputOffset = 0;
    if (this.tail !== null) {
      source[0] = this.tail;
      inputOffset = 1;
    }
    source.set(input, inputOffset);

    const values = [];
    while (this.position + 1 < source.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const sample =
        source[left] + (source[left + 1] - source[left]) * fraction;
      const clamped = Math.max(-1, Math.min(1, sample));
      values.push(clamped < 0 ? clamped * 32768 : clamped * 32767);
      this.position += this.ratio;
    }
    this.position -= source.length - 1;
    this.tail = source[source.length - 1];
    return Int16Array.from(values, Math.round);
  }
}
