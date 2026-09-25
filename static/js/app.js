const state = {
    csrf: "",
    user: null,
    patients: [],
    diseases: [],
    patient: null,
    encounter: null,
    soap: null,
    selectedCandidates: {},
    pollTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatDate(value, withTime = false) {
    if (!value) return "날짜 미상";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ko-KR", {
        year: "numeric", month: "short", day: "numeric",
        ...(withTime ? {hour: "2-digit", minute: "2-digit"} : {}),
    }).format(date);
}

function toast(message, type = "info") {
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    node.textContent = message;
    $("#toast-region").append(node);
    setTimeout(() => node.remove(), 4200);
}

async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.csrf && ["POST", "PUT", "PATCH", "DELETE"].includes(options.method || "GET")) {
        headers.set("X-CSRF-Token", state.csrf);
    }
    if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {
        headers.set("Content-Type", "application/json");
        options.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, {...options, headers, credentials: "same-origin"});
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
        if (response.status === 401 && path !== "/api/auth/login") showLogin();
        const error = new Error(data.message || data.error || `HTTP ${response.status}`);
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

function formObject(form) {
    return Object.fromEntries(new FormData(form).entries());
}

function showLogin() {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    state.csrf = "";
    $("#login-view").classList.remove("hidden");
    $("#app-view").classList.add("hidden");
}

async function showApp(user) {
    state.user = user;
    state.csrf = user.csrf_token;
    $("#user-name").textContent = user.username;
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    await Promise.all([loadPatients(), loadDiseases(), loadAdmin()]);
    state.pollTimer = setInterval(loadAdmin, 5000);
}

async function bootstrap() {
    try {
        await showApp(await api("/api/auth/me"));
    } catch {
        showLogin();
    }
}

$("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    $("#login-error").textContent = "";
    let user;
    try {
        user = await api("/api/auth/login", {method: "POST", body: formObject(form)});
    } catch (error) {
        $("#login-error").textContent = error.status === 429
            ? "로그인 시도가 잠시 제한되었습니다."
            : "사용자명 또는 비밀번호를 확인하세요.";
        return;
    }
    form.reset();
    try {
        await showApp(user);
    } catch (error) {
        console.error("Initial data loading failed", error);
        window.location.reload();
    }
});

$("#logout-button").addEventListener("click", async () => {
    try { await api("/api/auth/logout", {method: "POST"}); } finally { showLogin(); }
});

async function loadPatients(query = "") {
    const data = await api(`/api/patients${query ? `?q=${encodeURIComponent(query)}` : ""}`);
    state.patients = data.items;
    renderPatients();
}

function renderPatients() {
    const root = $("#patient-list");
    root.innerHTML = state.patients.length ? state.patients.map((patient) => `
        <div class="patient-item ${state.patient?.id === patient.id ? "active" : ""}" data-patient="${patient.id}">
            <strong>${escapeHtml(patient.name)}</strong>
            <span>${escapeHtml(patient.chart_number)} · ${escapeHtml(patient.species)} · 진료 ${patient.encounter_count}회</span>
        </div>
    `).join("") : `<p class="muted small">등록된 환자가 없습니다.</p>`;
    $$("[data-patient]", root).forEach((node) => node.addEventListener("click", () => selectPatient(node.dataset.patient)));
}

let searchTimer;
$("#patient-search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadPatients(event.target.value).catch(handleError), 250);
});

$("#toggle-patient-form").addEventListener("click", () => $("#patient-form").classList.toggle("hidden"));
$('[data-cancel="patient"]').addEventListener("click", () => $("#patient-form").classList.add("hidden"));

$("#patient-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formObject(form);
    payload.neutered = payload.neutered === "" ? null : payload.neutered === "true";
    try {
        const patient = await api("/api/patients", {method: "POST", body: payload});
        form.reset();
        form.classList.add("hidden");
        await loadPatients();
        await selectPatient(patient.id);
        toast("환자를 등록했습니다.");
    } catch (error) { handleError(error); }
});

async function selectPatient(patientId) {
    state.patient = await api(`/api/patients/${patientId}`);
    state.encounter = null;
    state.soap = null;
    $("#empty-state").classList.add("hidden");
    $("#patient-workspace").classList.remove("hidden");
    $("#encounter-workspace").classList.add("hidden");
    $("#patient-chart").textContent = state.patient.chart_number;
    $("#patient-title").textContent = state.patient.name;
    $("#patient-meta").textContent = [
        state.patient.species, state.patient.breed,
        state.patient.sex === "male" ? "수컷" : state.patient.sex === "female" ? "암컷" : "성별 미상",
        state.patient.weight_kg ? `${state.patient.weight_kg} kg` : null,
    ].filter(Boolean).join(" · ");
    renderPatients();
    renderEncounters();
}

function renderEncounters() {
    const root = $("#encounter-list");
    const encounters = state.patient?.encounters || [];
    root.innerHTML = encounters.length ? encounters.map((encounter) => `
        <div class="encounter-card ${state.encounter?.id === encounter.id ? "active" : ""}" data-encounter="${encounter.id}">
            <time>${escapeHtml(formatDate(encounter.visit_at))}</time>
            <div><strong>${escapeHtml(encounter.disease_name || "질병 미분류")}</strong><div class="muted small">${escapeHtml(encounter.chief_complaint || "주호소 없음")}</div></div>
            <span class="status ${encounter.status}">${encounter.status === "completed" ? "완료" : "작성 중"}</span>
        </div>
    `).join("") : `<p class="muted small">진료 이력이 없습니다. 새 진료를 생성하세요.</p>`;
    $$("[data-encounter]", root).forEach((node) => node.addEventListener("click", () => openEncounter(node.dataset.encounter)));
}

$("#new-encounter-button").addEventListener("click", () => {
    const input = $('#encounter-form [name="visit_at"]');
    const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
    input.value = now.toISOString().slice(0, 16);
    $("#encounter-form").classList.remove("hidden");
});
$('[data-cancel="encounter"]').addEventListener("click", () => $("#encounter-form").classList.add("hidden"));

$("#encounter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formObject(form);
    payload.patient_id = state.patient.id;
    try {
        const encounter = await api("/api/encounters", {method: "POST", body: payload});
        form.reset();
        form.classList.add("hidden");
        await selectPatient(state.patient.id);
        await openEncounter(encounter.id);
        toast("새 진료를 생성했습니다.");
    } catch (error) { handleError(error); }
});

async function openEncounter(encounterId) {
    const [encounter, soap] = await Promise.all([
        api(`/api/encounters/${encounterId}`),
        api(`/api/encounters/${encounterId}/soap`),
    ]);
    state.encounter = encounter;
    state.soap = soap;
    state.selectedCandidates = {};
    $("#encounter-workspace").classList.remove("hidden");
    renderEncounterHeader();
    populateEncounterForm();
    reflectEncounterStatus();
    renderEncounters();
    renderXrays();
    renderSoap();
    $("#encounter-workspace").scrollIntoView({behavior: "smooth", block: "start"});
}

function renderEncounterHeader() {
    if (!state.encounter) return;
    $("#encounter-date").textContent = formatDate(state.encounter.visit_at, true);
    $("#encounter-title").textContent = state.encounter.disease_name || "미분류 심장 진료";
    $("#encounter-summary").textContent = state.encounter.chief_complaint || "주호소가 입력되지 않았습니다.";
}

function encounterNeedsWriting(encounter) {
    return ["chief_complaint", "history_text", "physical_exam"].some((key) => !String(encounter[key] || "").trim());
}

function syncEncounterDetails(expanded = encounterNeedsWriting(state.encounter)) {
    const section = $("#encounter-details");
    section.classList.toggle("expanded", expanded);
    section.classList.toggle("collapsed", !expanded);
    $("#encounter-details-toggle").setAttribute("aria-expanded", String(expanded));
    const preview = [state.encounter.disease_name, state.encounter.chief_complaint]
        .map((value) => String(value || "").replace(/\s+/g, " ").trim())
        .filter(Boolean)
        .join(" · ");
    $("#encounter-details-preview").textContent = preview;
}

function populateEncounterForm() {
    if (!state.encounter) return;
    const form = $("#encounter-edit-form");
    form.elements.disease_id.value = state.encounter.disease_id || "";
    form.elements.visit_at.value = String(state.encounter.visit_at || "").slice(0, 16);
    form.elements.chief_complaint.value = state.encounter.chief_complaint || "";
    form.elements.history_text.value = state.encounter.history_text || "";
    form.elements.physical_exam.value = state.encounter.physical_exam || "";
    syncEncounterDetails();
}

$("#encounter-details-toggle").addEventListener("click", () => {
    syncEncounterDetails(!$("#encounter-details").classList.contains("expanded"));
});

function reflectEncounterStatus() {
    if (!state.encounter) return;
    const cached = state.patient?.encounters?.find((item) => item.id === state.encounter.id);
    if (cached) cached.status = state.encounter.status;
    $("#encounter-status").textContent = state.encounter.status === "completed" ? "SOAP 완료" : "작성 중";
    $("#encounter-status").className = `status ${state.encounter.status}`;
}

async function loadDiseases() {
    const data = await api("/api/diseases");
    state.diseases = data.items;
    $("#disease-list").innerHTML = data.items.length
        ? data.items.map((item) => `<span class="tag">${escapeHtml(item.name)}</span>`).join("")
        : `<span class="muted small">질병을 추가하세요.</span>`;
    const options = `<option value="">미선택</option>` + data.items.map((item) =>
        `<option value="${item.id}">${escapeHtml(item.category)} · ${escapeHtml(item.name)}</option>`
    ).join("");
    $("#encounter-disease").innerHTML = options;
    $("#encounter-edit-disease").innerHTML = options;
    if (state.encounter) $("#encounter-edit-disease").value = state.encounter.disease_id || "";
}

$("#encounter-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.encounter) return;
    const form = event.currentTarget;
    const button = $("button", form);
    button.disabled = true;
    try {
        state.encounter = await api(`/api/encounters/${state.encounter.id}`, {
            method: "PATCH",
            body: formObject(form),
        });
        const cached = state.patient?.encounters?.find((item) => item.id === state.encounter.id);
        if (cached) Object.assign(cached, state.encounter);
        state.soap = await api(`/api/encounters/${state.encounter.id}/soap`);
        renderEncounterHeader();
        reflectEncounterStatus();
        renderEncounters();
        renderSoap();
        syncEncounterDetails();
        toast("진료 기본정보를 저장했습니다.");
    } catch (error) {
        handleError(error);
    } finally {
        button.disabled = false;
    }
});

$("#disease-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
        await api("/api/diseases", {method: "POST", body: formObject(form)});
        form.elements.name.value = "";
        await loadDiseases();
        toast("질병 분류를 추가했습니다.");
    } catch (error) { handleError(error); }
});

$("#xray-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.encounter) return;
    const form = event.currentTarget;
    const button = $("button", form);
    button.disabled = true;
    try {
        await api(`/api/encounters/${state.encounter.id}/xrays`, {
            method: "POST", body: new FormData(form),
        });
        form.reset();
        state.encounter = await api(`/api/encounters/${state.encounter.id}`);
        renderXrays();
        toast("X-ray와 판독 소견을 저장했습니다.");
    } catch (error) { handleError(error); }
    finally { button.disabled = false; }
});

function renderXrays() {
    const root = $("#xray-list");
    const xrays = state.encounter?.xrays || [];
    root.innerHTML = xrays.length ? xrays.map((xray) => `
        <article class="xray-card" data-xray="${xray.id}">
            <a href="${xray.file_url}" target="_blank" rel="noopener"><img src="${xray.file_url}" alt="${escapeHtml(xray.original_name)}"></a>
            <div>
                <strong>${escapeHtml(xray.body_region || xray.original_name)}</strong>
                <textarea rows="3" aria-label="판독 소견">${escapeHtml(xray.reading_text || "")}</textarea>
                <button class="secondary save-reading">소견 저장</button>
            </div>
        </article>
    `).join("") : `<p class="muted small">등록된 X-ray가 없습니다.</p>`;
    $$(".save-reading", root).forEach((button) => button.addEventListener("click", async () => {
        const card = button.closest("[data-xray]");
        try {
            await api(`/api/xrays/${card.dataset.xray}`, {
                method: "PATCH", body: {reading_text: $("textarea", card).value},
            });
            toast("판독 소견을 저장했습니다.");
        } catch (error) { handleError(error); }
    }));
}

const stageNames = {S: "Subjective", O: "Objective", A: "Assessment", P: "Plan"};

function evidenceHtml(items = []) {
    if (!items.length) return "";
    return `<div class="evidence"><strong>근거</strong>${items.map((item) => {
        const pages = item.page_start ? ` p.${item.page_start}${item.page_end && item.page_end !== item.page_start ? `–${item.page_end}` : ""}` : "";
        return `<div>${escapeHtml(item.document_name)}${pages} · ${escapeHtml(item.excerpt.slice(0, 180))}</div>`;
    }).join("")}</div>`;
}

function focusStage(sections) {
    return sections.find((section) => section.unlocked && section.status !== "confirmed")?.stage || null;
}

function renderSoap() {
    const root = $("#soap-steps");
    const focus = focusStage(state.soap.sections);
    root.innerHTML = state.soap.sections.map((section) => {
        const stale = section.status === "stale";
        const current = section.current_text || "";
        const preview = current.replace(/\s+/g, " ").trim();
        const expanded = section.stage === focus;
        const candidatesOpen = expanded && (section.status === "generated" || section.status === "stale");
        const candidates = section.candidates.map((candidate) => `
            <div class="candidate ${candidate.is_selected ? "selected" : ""}" data-candidate="${candidate.id}">
                <p>${escapeHtml(candidate.content)}</p>
                <button class="ghost choose-candidate">이 후보 사용</button>
                ${evidenceHtml(candidate.evidence)}
            </div>
        `).join("");
        return `
            <article class="soap-step ${expanded ? "expanded" : "collapsed"} ${section.unlocked ? "" : "locked"}" data-stage="${section.stage}">
                <button type="button" class="soap-step-header" aria-expanded="${expanded}">
                    <span class="stage-letter">${section.stage}</span>
                    <strong>${stageNames[section.stage]}</strong>
                    ${preview ? `<span class="soap-preview">${escapeHtml(preview)}</span>` : ""}
                    <span class="status ${section.status === "confirmed" ? "completed" : ""}">${escapeHtml(section.status)}</span>
                    <span class="soap-chevron" aria-hidden="true"></span>
                </button>
                <div class="soap-step-body">
                    ${!section.unlocked ? `<p class="muted small">이전 단계를 확정하면 활성화됩니다.</p>` : ""}
                    ${stale ? `<p class="stale-note">이전 단계가 수정되어 재검토가 필요합니다.</p>` : ""}
                    <button class="secondary generate" ${section.unlocked ? "" : "disabled"}>${section.candidates.length ? "후보 다시 생성" : "Top N 후보 생성"}</button>
                    ${section.candidates.length ? `
                        <details class="candidates-fold" ${candidatesOpen ? "open" : ""}>
                            <summary>후보 ${section.candidates.length}개</summary>
                            <div class="candidates">${candidates}</div>
                        </details>
                    ` : ""}
                    <label>확정할 내용<textarea class="soap-editor" rows="6" ${section.unlocked ? "" : "disabled"}>${escapeHtml(current)}</textarea></label>
                    <button class="primary confirm" ${section.unlocked ? "" : "disabled"}>수정 내용 확정</button>
                </div>
            </article>
        `;
    }).join("");
    $$(".soap-step", root).forEach(bindSoapStep);
}

function bindSoapStep(step) {
    const stage = step.dataset.stage;
    const header = $(".soap-step-header", step);
    header.addEventListener("click", () => {
        const expanded = !step.classList.contains("expanded");
        step.classList.toggle("expanded", expanded);
        step.classList.toggle("collapsed", !expanded);
        header.setAttribute("aria-expanded", String(expanded));
    });
    $(".generate", step).addEventListener("click", async (event) => {
        event.currentTarget.disabled = true;
        event.currentTarget.textContent = "AI 생성 중…";
        try {
            await api(`/api/encounters/${state.encounter.id}/soap/${stage}/candidates`, {method: "POST"});
            state.soap = await api(`/api/encounters/${state.encounter.id}/soap`);
            state.encounter = await api(`/api/encounters/${state.encounter.id}`);
            reflectEncounterStatus();
            renderEncounters();
            renderSoap();
            toast(`${stage} 후보를 생성했습니다.`);
        } catch (error) { handleError(error); renderSoap(); }
    });
    $$(".choose-candidate", step).forEach((button) => button.addEventListener("click", () => {
        const card = button.closest("[data-candidate]");
        state.selectedCandidates[stage] = card.dataset.candidate;
        $(".soap-editor", step).value = $("p", card).textContent;
        $$(".candidate", step).forEach((item) => item.classList.toggle("selected", item === card));
    }));
    $(".confirm", step).addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const content = $(".soap-editor", step).value.trim();
        if (!content) return toast("확정할 내용을 입력하세요.", "error");
        button.disabled = true;
        try {
            state.soap = await api(`/api/encounters/${state.encounter.id}/soap/${stage}/confirm`, {
                method: "PUT",
                body: {content, candidate_id: state.selectedCandidates[stage] || null},
            });
            state.encounter = await api(`/api/encounters/${state.encounter.id}`);
            reflectEncounterStatus();
            renderSoap();
            renderEncounters();
            toast(`${stage} 단계를 확정했습니다.`);
        } catch (error) { handleError(error); button.disabled = false; }
    });
}

async function loadAdmin() {
    if (!state.user) return;
    try {
        const [status, jobs] = await Promise.all([api("/api/admin/status"), api("/api/admin/index-jobs")]);
        $("#model-badge").textContent = `${status.ai.provider} · ${status.ai.model}`;
        $("#knowledge-summary").innerHTML = `
            PDF ${status.knowledge_files.length}개 · Chunk ${status.counts.kb_chunks}개<br>
            Qdrant ${status.qdrant.ok ? "정상" : "연결 안 됨"}
        `;
        renderJobs(jobs.items);
    } catch (error) {
        if (error.status !== 401) console.warn("Admin status unavailable", error);
    }
}

function renderJobs(jobs) {
    $("#index-jobs").innerHTML = jobs.slice(0, 5).map((job) => {
        const done = job.processed_files + job.skipped_files + job.failed_files;
        const max = Math.max(job.total_files, 1);
        return `<div class="job"><strong>${job.job_type === "full" ? "전체" : "증분"} · ${escapeHtml(job.status)}</strong>
            <div>${done}/${job.total_files} 파일${job.failed_files ? ` · 실패 ${job.failed_files}` : ""}</div>
            <progress value="${done}" max="${max}"></progress>
            ${job.error_message ? `<div class="error">${escapeHtml(job.error_message)}</div>` : ""}
        </div>`;
    }).join("");
}

async function requestIndex(type) {
    if (type === "full" && !confirm("전체 문서를 새 컬렉션에 다시 임베딩합니다. 계속할까요?")) return;
    try {
        await api("/api/admin/index-jobs", {method: "POST", body: {type}});
        toast(type === "full" ? "전체 재색인을 요청했습니다." : "증분 갱신을 요청했습니다.");
        await loadAdmin();
    } catch (error) { handleError(error); }
}

$("#incremental-index").addEventListener("click", () => requestIndex("incremental"));
$("#full-index").addEventListener("click", () => requestIndex("full"));

function handleError(error) {
    console.error(error);
    const messages = {
        previous_stage_not_confirmed: "이전 SOAP 단계를 먼저 확정하세요.",
        index_job_already_running: "이미 실행 중인 색인 작업이 있습니다.",
        ai_generation_failed: error.message,
        conflict: "이미 사용 중인 값이거나 참조 중인 데이터입니다.",
    };
    toast(messages[error.data?.error] || error.message || "요청을 처리하지 못했습니다.", "error");
}

bootstrap();
