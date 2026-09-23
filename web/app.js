const elements = {
  form: document.querySelector("#answer-form"),
  answer: document.querySelector("#answer-input"),
  evaluate: document.querySelector("#evaluate-answer"),
  fakeMicrophone: document.querySelector("#fake-microphone"),
  next: document.querySelector("#next-card"),
  close: document.querySelector("#close-session"),
  cardKind: document.querySelector("#card-kind"),
  cardTitle: document.querySelector("#card-title"),
  cardPrompt: document.querySelector("#card-prompt"),
  contentBadge: document.querySelector("#content-badge"),
  termList: document.querySelector("#term-list"),
  image: document.querySelector("#card-image"),
  imageCanvas: document.querySelector("#card-image-canvas"),
  imageCaption: document.querySelector("#card-image-caption"),
  audio: document.querySelector("#card-audio"),
  audioButton: document.querySelector("#play-card-audio"),
  audioLabel: document.querySelector("#card-audio-label"),
  evaluation: document.querySelector("#evaluation"),
  verdictMark: document.querySelector("#verdict-mark"),
  verdictLabel: document.querySelector("#verdict-label"),
  evaluationTitle: document.querySelector("#evaluation-title"),
  evaluationExplanation: document.querySelector("#evaluation-explanation"),
  covered: document.querySelector("#covered-concepts"),
  missing: document.querySelector("#missing-concepts"),
  answerRequest: document.querySelector("#request-official-answer"),
  officialAnswer: document.querySelector("#official-answer"),
  officialAnswerCopy: document.querySelector("#official-answer-copy"),
  phaseKicker: document.querySelector("#phase-kicker"),
  phaseStatus: document.querySelector("#phase-status"),
  progressLabel: document.querySelector("#progress-label"),
  progressPercent: document.querySelector("#progress-percent"),
  progressBar: document.querySelector("#progress-bar"),
  studyCard: document.querySelector("#study-card"),
  help: document.querySelector("#help-button"),
  closeHelp: document.querySelector("#close-help"),
  dialog: document.querySelector("#about-dialog"),
};

const state = {
  sessionId: null,
  currentCard: null,
  mediaAudioUrl: null,
  closed: false,
};

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `A demonstração respondeu com ${response.status}.`);
  }
  return payload;
}

function setPhase(kicker, message) {
  elements.phaseKicker.textContent = kicker;
  elements.phaseStatus.textContent = message;
}

function listItems(container, values, emptyCopy) {
  container.replaceChildren();
  const content = values.length ? values : [emptyCopy];
  for (const value of content) {
    const item = document.createElement("li");
    item.textContent = value;
    container.append(item);
  }
}

function resetAnswerState() {
  elements.answer.value = "";
  elements.answer.disabled = false;
  elements.evaluate.disabled = false;
  elements.fakeMicrophone.disabled = false;
  elements.next.disabled = true;
  elements.evaluation.hidden = true;
  elements.officialAnswer.hidden = true;
  elements.answerRequest.disabled = false;
  elements.answerRequest.textContent = "Mostrar resposta oficial";
}

function renderMedia(card) {
  elements.image.hidden = true;
  elements.audio.hidden = true;
  elements.imageCanvas.replaceChildren();
  state.mediaAudioUrl = null;

  const image = card.media.find((item) => item.kind === "image");
  if (image) {
    const rendered = document.createElement("img");
    rendered.src = image.url;
    rendered.alt = image.alt_text;
    rendered.width = image.width || 480;
    rendered.height = image.height || 180;
    elements.imageCanvas.append(rendered);
    elements.imageCaption.textContent = image.alt_text;
    elements.image.hidden = false;
  }

  const audio = card.media.find((item) => item.kind === "audio");
  if (audio) {
    state.mediaAudioUrl = audio.url;
    elements.audioLabel.textContent = audio.description;
    elements.audio.hidden = false;
  }
}

function renderCard(payload) {
  const { card, position, total } = payload;
  state.currentCard = card;
  state.closed = false;
  elements.cardKind.textContent = card.label;
  elements.cardTitle.textContent = `Cartão ${position}`;
  elements.cardPrompt.replaceChildren();
  const prompt = document.createElement("p");
  prompt.textContent = card.prompt;
  elements.cardPrompt.append(prompt);
  elements.contentBadge.textContent = card.media.length ? "Com mídia sintética" : "Somente texto";

  elements.termList.replaceChildren();
  for (const term of card.terms) {
    const item = document.createElement("li");
    item.textContent = term;
    elements.termList.append(item);
  }
  elements.termList.hidden = card.terms.length === 0;
  renderMedia(card);
  resetAnswerState();

  const percentage = Math.round((position / total) * 100);
  elements.progressLabel.textContent = `Cartão ${position} de ${total}`;
  elements.progressPercent.textContent = `${percentage}%`;
  elements.progressBar.style.width = `${percentage}%`;
  setPhase("Sua vez", "Pense com calma; envie quando concluir.");
}

async function openSession(profile = "full") {
  setPhase("Preparando", "Abrindo a sessão local…");
  const payload = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ profile }),
  });
  state.sessionId = payload.session_id;
  renderCard(payload);
}

async function evaluateAnswer(event) {
  event.preventDefault();
  const answer = elements.answer.value.trim();
  if (!answer) {
    elements.answer.focus();
    setPhase("Resposta vazia", "Escreva ou simule uma resposta antes de avaliar.");
    return;
  }

  elements.evaluate.disabled = true;
  elements.fakeMicrophone.disabled = true;
  setPhase("Avaliando", "Comparando os conceitos localmente…");
  try {
    const payload = await api(`/api/sessions/${state.sessionId}/evaluation`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    });
    renderEvaluation(payload.evaluation);
  } catch (error) {
    elements.evaluate.disabled = false;
    elements.fakeMicrophone.disabled = false;
    setPhase("Não foi possível avaliar", error.message);
  }
}

function renderEvaluation(evaluation) {
  const copy = {
    correct: ["✓", "Correto", "Você cobriu os conceitos esperados."],
    partial: ["≈", "Parcialmente correto", "Boa direção; ainda há algo a recuperar."],
    incorrect: ["↺", "Vamos rever", "A resposta ainda não cobriu o núcleo do cartão."],
    ungradable: ["?", "Não avaliado", "A resposta não pôde ser avaliada."],
  }[evaluation.verdict];

  elements.verdictMark.textContent = copy[0];
  elements.verdictLabel.textContent = "Avaliação determinística";
  elements.evaluationTitle.textContent = copy[1];
  elements.evaluationExplanation.textContent = evaluation.feedback || copy[2];
  listItems(elements.covered, evaluation.covered_concepts, "Nenhum conceito ainda");
  listItems(elements.missing, evaluation.missing_concepts, "Nada — resposta completa");
  elements.evaluation.hidden = false;
  elements.next.disabled = false;
  elements.answer.disabled = true;
  setPhase("Avaliação pronta", "A resposta oficial continua oculta até você solicitar.");
  const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
  elements.evaluation.scrollIntoView({ behavior, block: "nearest" });
}

async function simulateMicrophone() {
  elements.fakeMicrophone.disabled = true;
  setPhase("Voz simulada", "Transcrevendo um exemplo local, sem usar o microfone…");
  try {
    const payload = await api(`/api/sessions/${state.sessionId}/transcription`, {
      method: "POST",
    });
    elements.answer.value = payload.transcript;
    elements.answer.focus();
    setPhase("Transcrição pronta", "Edite se quiser e envie quando concluir.");
  } catch (error) {
    setPhase("Transcrição indisponível", error.message);
  } finally {
    elements.fakeMicrophone.disabled = false;
  }
}

async function requestOfficialAnswer() {
  elements.answerRequest.disabled = true;
  setPhase("Leitura protegida", "Solicitando apenas o verso do cartão…");
  try {
    const payload = await api(`/api/sessions/${state.sessionId}/official-answer`, {
      method: "POST",
    });
    elements.officialAnswerCopy.textContent = payload.official_answer;
    elements.officialAnswer.hidden = false;
    elements.answerRequest.textContent = "Resposta oficial revelada";
    if (payload.speech_url) {
      const player = new Audio(payload.speech_url);
      await player.play().catch(() => undefined);
    }
    setPhase("Resposta oficial", "Verso revelado somente após seu pedido.");
  } catch (error) {
    elements.answerRequest.disabled = false;
    setPhase("Resposta indisponível", error.message);
  }
}

async function nextCard() {
  elements.next.disabled = true;
  setPhase("Avançando", "Registrando a revisão local…");
  try {
    const payload = await api(`/api/sessions/${state.sessionId}/next`, {
      method: "POST",
    });
    if (payload.complete) {
      await closeSession();
      return;
    }
    renderCard(payload);
    elements.studyCard.focus();
  } catch (error) {
    elements.next.disabled = false;
    setPhase("Não foi possível avançar", error.message);
  }
}

async function closeSession() {
  if (state.closed || !state.sessionId) return;
  setPhase("Sincronizando", "Confirmando o encerramento simulado…");
  try {
    const payload = await api(`/api/sessions/${state.sessionId}/close`, {
      method: "POST",
    });
    state.closed = true;
    elements.answer.disabled = true;
    elements.evaluate.disabled = true;
    elements.fakeMicrophone.disabled = true;
    elements.next.disabled = true;
    elements.close.disabled = true;
    setPhase("Sessão encerrada", payload.message);
  } catch (error) {
    setPhase("Não foi possível encerrar", error.message);
  }
}

async function switchProfile(event) {
  if (!event.target.matches('input[name="study-mode"]')) return;
  if (state.sessionId && !state.closed) {
    await closeSession();
  }
  elements.close.disabled = false;
  await openSession(event.target.value).catch((error) => {
    setPhase("Não foi possível abrir", error.message);
  });
}

elements.form.addEventListener("submit", evaluateAnswer);
elements.fakeMicrophone.addEventListener("click", simulateMicrophone);
elements.answerRequest.addEventListener("click", requestOfficialAnswer);
elements.next.addEventListener("click", nextCard);
elements.close.addEventListener("click", closeSession);
elements.audioButton.addEventListener("click", async () => {
  if (!state.mediaAudioUrl) return;
  await new Audio(state.mediaAudioUrl).play().catch(() => {
    setPhase("Áudio bloqueado", "O navegador pediu uma nova interação para tocar o áudio.");
  });
});
document.querySelector(".mode-picker").addEventListener("change", switchProfile);
elements.help.addEventListener("click", () => elements.dialog.showModal());
elements.closeHelp.addEventListener("click", () => elements.dialog.close());

openSession().catch((error) => {
  setPhase("Demo indisponível", error.message);
});
