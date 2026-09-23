import assert from "node:assert/strict";
import test from "node:test";

import { PlaybackHandshake } from "../../web/audio/playback-handshake.mjs";

const started = (handshake, generationId = 1, utteranceId = "utterance-1") =>
  handshake.begin({
    generationId,
    utteranceId,
    utterancePolicy: "locked",
  });

test("producer end and matching local drain are both required", () => {
  const handshake = new PlaybackHandshake(1);
  assert.equal(started(handshake), true);
  assert.equal(
    handshake.noteAudio({
      generationId: 1,
      utteranceId: "utterance-1",
      audioSequence: 1,
    }),
    true,
  );

  assert.equal(
    handshake.locallyDrained({
      generationId: 1,
      utteranceId: "utterance-1",
      audioSequence: 1,
    }),
    null,
  );
  assert.deepEqual(
    handshake.producerEnded({
      generationId: 1,
      utteranceId: "utterance-1",
    }),
    {
      generationId: 1,
      utteranceId: "utterance-1",
      utterancePolicy: "locked",
    },
  );
});

test("temporary underflow is invalidated by the next streamed segment", () => {
  const handshake = new PlaybackHandshake(1);
  started(handshake);
  handshake.noteAudio({
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 1,
  });
  assert.equal(
    handshake.locallyDrained({
      generationId: 1,
      utteranceId: "utterance-1",
      audioSequence: 1,
    }),
    null,
  );

  handshake.noteAudio({
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 2,
  });
  assert.equal(
    handshake.producerEnded({
      generationId: 1,
      utteranceId: "utterance-1",
    }),
    null,
  );
  assert.equal(
    handshake.locallyDrained({
      generationId: 1,
      utteranceId: "utterance-1",
      audioSequence: 1,
    }),
    null,
  );
  assert.deepEqual(
    handshake.locallyDrained({
      generationId: 1,
      utteranceId: "utterance-1",
      audioSequence: 2,
    }),
    {
      generationId: 1,
      utteranceId: "utterance-1",
      utterancePolicy: "locked",
    },
  );
});

test("cancel and generation advance ignore stale producer and worklet events", () => {
  const handshake = new PlaybackHandshake(1);
  started(handshake);
  handshake.noteAudio({
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 1,
  });

  assert.equal(handshake.setGeneration(2), true);
  assert.equal(
    handshake.producerEnded({
      generationId: 1,
      utteranceId: "utterance-1",
    }),
    null,
  );
  assert.equal(
    handshake.locallyDrained({
      generationId: 1,
      utteranceId: "utterance-1",
      audioSequence: 1,
    }),
    null,
  );

  assert.equal(started(handshake, 2, "utterance-2"), true);
  handshake.noteAudio({
    generationId: 2,
    utteranceId: "utterance-2",
    audioSequence: 1,
  });
  handshake.producerEnded({ generationId: 2, utteranceId: "utterance-2" });
  assert.equal(
    handshake.locallyDrained({
      generationId: 2,
      utteranceId: "utterance-1",
      audioSequence: 1,
    }),
    null,
  );
  assert.equal(
    handshake.locallyDrained({
      generationId: 2,
      utteranceId: "utterance-2",
      audioSequence: 1,
    })?.utteranceId,
    "utterance-2",
  );
});

test("same-generation reconnect preserves a ready but unacknowledged boundary", () => {
  const handshake = new PlaybackHandshake(4);
  started(handshake, 4, "utterance-reconnect");
  handshake.noteAudio({
    generationId: 4,
    utteranceId: "utterance-reconnect",
    audioSequence: 1,
  });
  handshake.producerEnded({
    generationId: 4,
    utteranceId: "utterance-reconnect",
  });
  const ready = handshake.locallyDrained({
    generationId: 4,
    utteranceId: "utterance-reconnect",
    audioSequence: 1,
  });

  assert.equal(handshake.setGeneration(4), true);
  assert.deepEqual(handshake.acknowledgement(), ready);
  assert.equal(handshake.markAcknowledged(ready), true);
  assert.equal(handshake.acknowledgement(), null);
});

test("completion has no duration fallback", () => {
  const handshake = new PlaybackHandshake(1);
  started(handshake);
  handshake.noteAudio({
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 1,
  });
  handshake.producerEnded({ generationId: 1, utteranceId: "utterance-1" });

  assert.equal(handshake.acknowledgement(), null);
});

test("audio after producer end fails closed", () => {
  const handshake = new PlaybackHandshake(1);
  started(handshake);
  handshake.noteAudio({
    generationId: 1,
    utteranceId: "utterance-1",
    audioSequence: 1,
  });
  handshake.producerEnded({ generationId: 1, utteranceId: "utterance-1" });

  assert.throws(
    () =>
      handshake.noteAudio({
        generationId: 1,
        utteranceId: "utterance-1",
        audioSequence: 2,
      }),
    /after producer end/,
  );
});
