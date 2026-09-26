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

function speciesLabel(value) {
    return {Canine: "개", Feline: "고양이"}[value] || value || "종 미상";
}

function sexLabel(sex, neutered) {
    if (sex === "male") return neutered ? "수컷중성화" : "수컷";
    if (sex === "female") return neutered ? "암컷중성화" : "암컷";
    return "성별";
}

function formatOwnerPhoneInput(value) {
    const digits = String(value || "").replace(/\D/g, "");
    const subscriber = (digits.startsWith("010") ? digits.slice(3) : digits).slice(0, 8);
    if (!subscriber) return "010-";
    if (subscriber.length <= 4) return `010-${subscriber}`;
    return `010-${subscriber.slice(0, 4)}-${subscriber.slice(4)}`;
}

function padDatePart(value) {
    return String(value).padStart(2, "0");
}

function dateToISO(date) {
    return `${date.getFullYear()}-${padDatePart(date.getMonth() + 1)}-${padDatePart(date.getDate())}`;
}

function dateToKoreanInput(date) {
    return `${date.getFullYear()}/${padDatePart(date.getMonth() + 1)}/${padDatePart(date.getDate())}`;
}

function validLocalDate(year, month, day) {
    const date = new Date(year, month - 1, day);
    return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
        ? date
        : null;
}

function subtractAgeFromToday(amount, unit) {
    const today = new Date();
    if (unit === "d") {
        const date = new Date(today.getFullYear(), today.getMonth(), today.getDate());
        date.setDate(date.getDate() - amount);
        return date;
    }

    let year = today.getFullYear();
    let month = today.getMonth();
    if (unit === "y") year -= amount;
    if (unit === "m") {
        const totalMonths = year * 12 + month - amount;
        year = Math.floor(totalMonths / 12);
        month = totalMonths % 12;
    }
    const lastDay = new Date(year, month + 1, 0).getDate();
    return new Date(year, month, Math.min(today.getDate(), lastDay));
}

function parseBirthDateText(value) {
    const text = String(value || "").trim().toLowerCase();
    const ageMatch = text.match(/^(\d+)([ymd])$/);
    if (ageMatch) {
        const amount = Number(ageMatch[1]);
        if (amount <= 36500) return subtractAgeFromToday(amount, ageMatch[2]);
        return null;
    }

    const dateMatch = text.match(/^(\d{4})[\/.\-](\d{1,2})[\/.\-](\d{1,2})$/);
    if (!dateMatch) return null;
    return validLocalDate(Number(dateMatch[1]), Number(dateMatch[2]), Number(dateMatch[3]));
}

function dateFromISO(value) {
    if (!value) return null;
    const parts = value.split("-").map(Number);
    return parts.length === 3 ? validLocalDate(parts[0], parts[1], parts[2]) : null;
}

function formatPatientAge(value) {
    const birthDate = dateFromISO(value);
    if (!birthDate) return null;

    const today = new Date();
    const todayDate = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    if (birthDate > todayDate) return null;

    let totalMonths = (
        (todayDate.getFullYear() - birthDate.getFullYear()) * 12
        + todayDate.getMonth()
        - birthDate.getMonth()
    );
    const anniversaryYear = birthDate.getFullYear() + Math.floor(
        (birthDate.getMonth() + totalMonths) / 12
    );
    const anniversaryMonth = (birthDate.getMonth() + totalMonths) % 12;
    const anniversaryDay = Math.min(
        birthDate.getDate(),
        new Date(anniversaryYear, anniversaryMonth + 1, 0).getDate(),
    );
    if (new Date(anniversaryYear, anniversaryMonth, anniversaryDay) > todayDate) {
        totalMonths -= 1;
    }
    if (totalMonths < 0) return null;
    const years = Math.floor(totalMonths / 12);
    const months = totalMonths % 12;
    return months ? `나이: ${years}년 ${months}개월` : `나이: ${years}년`;
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
            <span class="patient-list-chart">차트번호 ${escapeHtml(patient.chart_number)}</span>
            <strong>${escapeHtml(patient.name)}</strong>
            <span>${escapeHtml(speciesLabel(patient.species))} · 진료 ${patient.encounter_count}회</span>
        </div>
    `).join("") : `<p class="muted small">등록된 환자가 없습니다.</p>`;
    $$("[data-patient]", root).forEach((node) => node.addEventListener("click", () => selectPatient(node.dataset.patient)));
}

let searchTimer;
$("#patient-search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadPatients(event.target.value).catch(handleError), 250);
});

$("#toggle-patient-form").addEventListener("click", async () => {
    const form = $("#patient-form");
    const isOpening = form.classList.contains("hidden");
    form.classList.toggle("hidden");
    if (!isOpening) return;

    const chartLabel = $("#new-patient-chart");
    chartLabel.textContent = "차트번호 확인 중…";
    form.querySelector('[name="name"]').focus();
    try {
        const data = await api("/api/patients/next-chart-number");
        chartLabel.textContent = `차트번호 ${data.chart_number}`;
    } catch (error) {
        chartLabel.textContent = "차트번호는 저장 시 자동 생성됩니다.";
        handleError(error);
    }
});
$('[data-cancel="patient"]').addEventListener("click", () => $("#patient-form").classList.add("hidden"));

function installOwnerPhoneFormatting(input) {
    input.addEventListener("focus", () => {
        if (!input.value) input.value = "010-";
    });
    input.addEventListener("input", () => {
        input.value = formatOwnerPhoneInput(input.value);
        input.setSelectionRange(input.value.length, input.value.length);
    });
    input.addEventListener("blur", () => {
        if (input.value === "010-") input.value = "";
    });
}

const ownerPhoneInput = $('#patient-form [name="owner_phone"]');
const patientContactPhoneInput = $('#patient-contact-form [name="owner_phone"]');
installOwnerPhoneFormatting(ownerPhoneInput);
installOwnerPhoneFormatting(patientContactPhoneInput);

$("#edit-patient-contact-button").addEventListener("click", () => {
    if (!state.patient) return;
    const form = $("#patient-contact-form");
    form.elements.owner_name.value = state.patient.owner_name || "";
    form.elements.owner_phone.value = state.patient.owner_phone || "";
    form.classList.remove("hidden");
    form.elements.owner_name.focus();
});
$("#cancel-patient-contact").addEventListener("click", () => {
    $("#patient-contact-form").classList.add("hidden");
});
$("#patient-contact-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const patientId = state.patient?.id;
    if (!patientId) return;
    try {
        await api(`/api/patients/${patientId}`, {
            method: "PATCH",
            body: formObject(form),
        });
        form.classList.add("hidden");
        await loadPatients();
        await selectPatient(patientId);
        toast("보호자 정보를 수정했습니다.");
    } catch (error) { handleError(error); }
});

const birthDateDisplay = $("#patient-birth-date-display");
const birthDateValue = $("#patient-birth-date");
const birthCalendar = $("#patient-birth-calendar");
const birthCalendarDays = $("#birth-calendar-days");
let birthCalendarCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);

function renderBirthCalendar() {
    const year = birthCalendarCursor.getFullYear();
    const month = birthCalendarCursor.getMonth();
    const today = new Date();
    const todayISO = dateToISO(today);
    const selectedISO = birthDateValue.value;
    $("#birth-calendar-title").textContent = `${year}년 ${month + 1}월`;
    $("#birth-calendar-today").textContent = `오늘: ${today.getFullYear()}년 ${today.getMonth() + 1}월 ${today.getDate()}일`;

    const firstWeekday = new Date(year, month, 1).getDay();
    const firstVisibleDate = new Date(year, month, 1 - firstWeekday);
    birthCalendarDays.innerHTML = "";
    for (let index = 0; index < 42; index += 1) {
        const date = new Date(
            firstVisibleDate.getFullYear(),
            firstVisibleDate.getMonth(),
            firstVisibleDate.getDate() + index,
        );
        const iso = dateToISO(date);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "calendar-day";
        button.dataset.date = iso;
        button.textContent = date.getDate();
        if (date.getMonth() !== month) button.classList.add("other-month");
        if (date.getDay() === 0) button.classList.add("sunday");
        if (date.getDay() === 6) button.classList.add("saturday");
        if (iso === todayISO) button.classList.add("today");
        if (iso === selectedISO) button.classList.add("selected");
        birthCalendarDays.append(button);
    }
}

function openBirthCalendar() {
    const selected = dateFromISO(birthDateValue.value);
    const base = selected || new Date();
    birthCalendarCursor = new Date(base.getFullYear(), base.getMonth(), 1);
    renderBirthCalendar();
    birthCalendar.classList.remove("hidden");
    birthDateDisplay.setAttribute("aria-expanded", "true");
}

function closeBirthCalendar() {
    birthCalendar.classList.add("hidden");
    birthDateDisplay.setAttribute("aria-expanded", "false");
}

function setBirthDate(date, close = false) {
    birthDateValue.value = dateToISO(date);
    birthDateDisplay.value = dateToKoreanInput(date);
    birthDateDisplay.setCustomValidity("");
    birthCalendarCursor = new Date(date.getFullYear(), date.getMonth(), 1);
    renderBirthCalendar();
    if (close) closeBirthCalendar();
}

function commitBirthDate() {
    const text = birthDateDisplay.value.trim();
    if (!text) {
        birthDateValue.value = "";
        birthDateDisplay.setCustomValidity("");
        return true;
    }
    const date = parseBirthDateText(text);
    if (!date) {
        birthDateDisplay.setCustomValidity("생년월일은 년/월/일 또는 1y, 1m, 25d 형식으로 입력하세요.");
        birthDateDisplay.reportValidity();
        return false;
    }
    setBirthDate(date);
    return true;
}

birthDateDisplay.addEventListener("focus", openBirthCalendar);
birthDateDisplay.addEventListener("input", () => {
    birthDateDisplay.setCustomValidity("");
    birthDateValue.value = "";
    const date = parseBirthDateText(birthDateDisplay.value);
    if (date) {
        birthDateValue.value = dateToISO(date);
        birthCalendarCursor = new Date(date.getFullYear(), date.getMonth(), 1);
        renderBirthCalendar();
    }
});
birthDateDisplay.addEventListener("blur", () => {
    commitBirthDate();
    closeBirthCalendar();
});
birthDateDisplay.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        if (commitBirthDate()) {
            closeBirthCalendar();
            birthDateDisplay.blur();
        }
    } else if (event.key === "Escape") {
        closeBirthCalendar();
        birthDateDisplay.blur();
    }
});

birthCalendar.addEventListener("mousedown", (event) => event.preventDefault());
birthCalendarDays.addEventListener("click", (event) => {
    const day = event.target.closest("[data-date]");
    if (!day) return;
    const date = dateFromISO(day.dataset.date);
    if (date) {
        setBirthDate(date, true);
        birthDateDisplay.blur();
    }
});
$("#birth-calendar-prev").addEventListener("click", () => {
    birthCalendarCursor = new Date(
        birthCalendarCursor.getFullYear(),
        birthCalendarCursor.getMonth() - 1,
        1,
    );
    renderBirthCalendar();
});
$("#birth-calendar-next").addEventListener("click", () => {
    birthCalendarCursor = new Date(
        birthCalendarCursor.getFullYear(),
        birthCalendarCursor.getMonth() + 1,
        1,
    );
    renderBirthCalendar();
});
$("#birth-calendar-today").addEventListener("click", () => {
    setBirthDate(new Date(), true);
    birthDateDisplay.blur();
});
$("#birth-calendar-toggle").addEventListener("click", () => {
    birthDateDisplay.focus();
    openBirthCalendar();
});
document.addEventListener("mousedown", (event) => {
    if (!event.target.closest(".birth-date-field")) closeBirthCalendar();
});

$("#patient-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!commitBirthDate()) {
        birthDateDisplay.focus();
        return;
    }
    const form = event.currentTarget;
    const payload = formObject(form);
    const [sex, neutered] = payload.sex_status.split(":");
    delete payload.sex_status;
    payload.sex = sex;
    payload.neutered = neutered === "true";
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
    const encounterForm = $("#encounter-form");
    encounterForm.reset();
    encounterForm.classList.add("hidden");
    $("#patient-contact-form").classList.add("hidden");
    $("#encounter-workspace").classList.add("hidden");
    $("#patient-chart").textContent = `차트번호 ${state.patient.chart_number}`;
    $("#patient-title").textContent = state.patient.name;
    $("#patient-meta").textContent = [
        speciesLabel(state.patient.species), state.patient.breed,
        sexLabel(state.patient.sex, state.patient.neutered),
        formatPatientAge(state.patient.birth_date),
        state.patient.owner_name ? `보호자 ${state.patient.owner_name}` : null,
        state.patient.owner_phone,
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
    const form = $("#encounter-form");
    form.reset();
    const input = $('#encounter-form [name="visit_at"]');
    const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
    input.value = now.toISOString().slice(0, 16);
    $("#new-encounter-options").open = false;
    $("#patient-contact-form").classList.add("hidden");
    $("#encounter-workspace").classList.add("hidden");
    form.classList.remove("hidden");
    form.scrollIntoView({behavior: "smooth", block: "center"});
    requestAnimationFrame(() => form.elements.chief_complaint.focus());
});
$('[data-cancel="encounter"]').addEventListener("click", () => {
    const form = $("#encounter-form");
    form.reset();
    form.classList.add("hidden");
    if (state.encounter) $("#encounter-workspace").classList.remove("hidden");
});

$("#encounter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button.primary", form);
    const payload = formObject(form);
    const patientId = state.patient.id;
    payload.patient_id = patientId;
    button.disabled = true;
    button.textContent = "진료 생성 중…";
    try {
        const encounter = await api("/api/encounters", {method: "POST", body: payload});
        form.reset();
        form.classList.add("hidden");
        await selectPatient(patientId);
        await openEncounter(encounter.id);
        toast("새 진료를 생성했습니다.");
        try {
            await generateSoapStage("S");
        } catch (error) {
            handleError(error);
            renderSoap();
        }
    } catch (error) {
        handleError(error);
    } finally {
        button.disabled = false;
        button.textContent = "S 초안 생성";
    }
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
    return !String(encounter?.chief_complaint || "").trim();
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

const stageNames = {S: "주관적 정보", O: "객관적 정보", A: "평가", P: "계획"};

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

async function generateSoapStage(stage) {
    const step = $(`.soap-step[data-stage="${stage}"]`);
    const button = step ? $(".generate", step) : null;
    if (button) button.disabled = true;
    if (button) button.textContent = "AI 생성 중…";
    await api(`/api/encounters/${state.encounter.id}/soap/${stage}/candidates`, {method: "POST"});
    state.soap = await api(`/api/encounters/${state.encounter.id}/soap`);
    state.encounter = await api(`/api/encounters/${state.encounter.id}`);
    reflectEncounterStatus();
    renderEncounters();
    renderSoap();
    toast(`${stage} AI 초안을 작성했습니다.`);
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
                <button class="ghost choose-candidate">이 초안 선택</button>
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
                    ${section.stage === "O" && section.unlocked ? `
                        <label>신체검사/기초 소견
                            <textarea class="objective-source" rows="4" placeholder="청진, 호흡수, 심박수, 활력징후 등">${escapeHtml(state.encounter?.physical_exam || "")}</textarea>
                        </label>
                    ` : ""}
                    <button class="secondary generate" ${section.unlocked ? "" : "disabled"}>${section.candidates.length ? "AI 초안 다시 작성" : `${section.stage} 초안 작성`}</button>
                    ${section.candidates.length ? `
                        <details class="candidates-fold" ${candidatesOpen ? "open" : ""}>
                            <summary>AI 작성 초안 ${section.candidates.length}개</summary>
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
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = "AI 생성 중…";
        try {
            if (stage === "O") {
                const physicalExam = $(".objective-source", step).value.trim();
                if (!physicalExam) {
                    toast("신체검사/기초 소견을 입력하세요.", "error");
                    button.disabled = false;
                    button.textContent = "O 초안 생성";
                    return;
                }
                if (physicalExam !== String(state.encounter.physical_exam || "").trim()) {
                    state.encounter = await api(`/api/encounters/${state.encounter.id}`, {
                        method: "PATCH",
                        body: {physical_exam: physicalExam},
                    });
                    state.soap = await api(`/api/encounters/${state.encounter.id}/soap`);
                    reflectEncounterStatus();
                    renderEncounters();
                }
            }
            await generateSoapStage(stage);
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
