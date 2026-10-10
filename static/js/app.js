const state = {
    csrf: "",
    user: null,
    patients: [],
    diseases: [],
    patient: null,
    encounter: null,
    soap: null,
    cardiacComparison: null,
    workflow: {ecg: null, lab: null, prescription: null, billing: null, echoVideos: []},
    trends: [],
    monitoring: [],
    revisions: [],
    appointments: [],
    dashboard: null,
    commercial: {workflow: null, diagnosticPlan: [], education: null, prescription: null, invoice: null},
    activeView: "today",
    selectedCandidates: {},
    pollTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const NOTEBOOKLM_URL = "https://notebook.google.com/";

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
    closeAdminDrawer();
    $("#login-view").classList.remove("hidden");
    $("#app-view").classList.add("hidden");
}

async function showApp(user) {
    state.user = user;
    state.csrf = user.csrf_token;
    $("#user-name").textContent = `${user.username} · ${user.role === "staff" ? "스태프" : "수의사"}`;
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    await Promise.all([loadPatients(), loadDiseases(), ...(user.role === "veterinarian" ? [loadAdmin()] : [])]);
    $("#admin-tools-button").classList.toggle("hidden", user.role !== "veterinarian");
    navigateView("today");
    if (user.role === "veterinarian") state.pollTimer = setInterval(loadAdmin, 5000);
}

function navigateView(view) {
    const page = $(`[data-page="${view}"]`);
    if (!page) return;
    state.activeView = view;
    $$(".app-page").forEach((item) => item.classList.toggle("active", item === page));
    $$(".primary-nav [data-view]").forEach((button) => {
        button.classList.toggle("active", button.dataset.view === view);
    });
    if (view === "today") loadTodayDashboard().catch(handleError);
    if (view === "diagnostics") renderDiagnostics();
    if (view === "results") renderTrends();
    if (view === "schedule") loadAppointments().catch(handleError);
}

$$('[data-view]').forEach((button) => button.addEventListener("click", () => {
    navigateView(button.dataset.view);
}));

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

async function loadTodayDashboard() {
    if (!state.user) return;
    const today = dateToISO(new Date());
    const data = await api(`/api/dashboard/today?date=${today}`);
    state.dashboard = data;
    const date = new Date(`${data.date}T00:00:00`);
    $("#today-title").textContent = `오늘 ${new Intl.DateTimeFormat("ko-KR", {
        month: "long", day: "numeric", weekday: "long",
    }).format(date)}`;
    const values = [
        ["오늘 진료", data.summary.total],
        ["완료", data.summary.completed],
        ["미완료", data.summary.draft],
        ["Echo 기록 필요", data.summary.echo_pending],
    ];
    $("#today-summary").innerHTML = values.map(([label, value]) => `
        <article><span>${label}</span><strong>${value}</strong></article>
    `).join("");
    const flowItems = [
        ...data.items.map((item) => ({...item, flowType: "encounter", flowAt: item.visit_at})),
        ...(data.appointments || []).map((item) => ({...item, flowType: "appointment", flowAt: item.starts_at})),
    ].sort((a, b) => new Date(a.flowAt) - new Date(b.flowAt));
    $("#today-encounters").innerHTML = flowItems.length ? flowItems.map((item) => item.flowType === "encounter" ? `
        <article class="today-row" data-today-encounter="${item.id}" data-patient="${item.patient_id}">
            <time>${escapeHtml(new Intl.DateTimeFormat("ko-KR", {hour: "2-digit", minute: "2-digit"}).format(new Date(item.visit_at)))}</time>
            <strong>${escapeHtml(item.patient_name)}</strong>
            <span>${escapeHtml(item.diagnosis_name)}</span>
            <span>${escapeHtml(item.chief_complaint || "주호소 없음")}</span>
            <span class="status ${item.status === "completed" ? "completed" : ""}">${item.status === "completed" ? "완료" : "진료 작성 중"}</span>
        </article>` : `
        <article class="today-row appointment" data-today-patient="${item.patient_id}">
            <time>${escapeHtml(new Intl.DateTimeFormat("ko-KR", {hour: "2-digit", minute: "2-digit"}).format(new Date(item.starts_at)))}</time>
            <strong>${escapeHtml(item.patient_name)}</strong>
            <span>예약</span>
            <span>${escapeHtml(item.purpose)}</span>
            <span class="status">${escapeHtml(appointmentStatusLabels[item.status] || item.status)}</span>${["scheduled", "arrived"].includes(item.status) && !item.encounter_id ? `<button type="button" class="primary" data-today-checkin="${item.id}" data-patient-id="${item.patient_id}">접수</button>` : ""}
        </article>`
    ).join("") : '<p class="empty-copy">오늘 진료 또는 예약이 없습니다.</p>';
    const scheduled = (data.appointments || []).filter((item) => item.status === "scheduled").length;
    const arrived = (data.appointments || []).filter((item) => item.status === "arrived").length;
    const unpaid = Number(data.billing?.unpaid_count || 0);
    const counters = [
        ["미완료 SOAP", data.summary.draft],
        ["Echo 기록 필요", data.summary.echo_pending],
        ["예약", scheduled],
        ["내원 대기", arrived],
        ["미수납", unpaid],
    ];
    const taskTypeLabels = {result_review: "검사결과", callback: "Callback", general: "일반"};
    $("#today-tasks").innerHTML = counters.map(([label, count]) => `
        <div class="task-item"><span><i class="task-dot"></i> ${label}</span><strong>${count}</strong></div>
    `).join("") + (data.tasks || []).map((item) => `
        <div class="task-item clinic-task ${item.status === "done" ? "done" : ""}"><span><i class="task-dot"></i> ${escapeHtml(taskTypeLabels[item.task_type] || item.task_type)} · ${escapeHtml(item.patient_name || "공통")}<small>${escapeHtml(item.title)}</small></span>${item.status === "open" ? `<button type="button" data-task-done="${item.id}">완료</button>` : "✓"}</div>
    `).join("");
    $("#today-visit-summary").innerHTML = `진료 ${data.summary.total}건 · 완료 ${data.summary.completed} / 작성 중 ${data.summary.draft}<br>수납 ${Number(data.billing?.paid_count || 0)}건 · 매출 <strong>${Number(data.billing?.paid_total || 0).toLocaleString("ko-KR")}원</strong>`;
    const daily = await api(`/api/billing/daily-close?date=${today}`);
    $("#daily-close-status").textContent = daily.closing
        ? `마감 완료 · 청구 ${Number(daily.summary.billed_amount).toLocaleString("ko-KR")}원 · 순결제 ${Number(daily.summary.net).toLocaleString("ko-KR")}원 · 차이 ${Number(daily.summary.billing_payment_difference).toLocaleString("ko-KR")}원`
        : `미마감 · 환불 ${Number(daily.summary.refunds).toLocaleString("ko-KR")}원 · 미수 ${Number(daily.summary.outstanding_amount).toLocaleString("ko-KR")}원 · 미청구 ${Number(daily.summary.unbilled_count)}건`;
    $("#close-day-button").disabled = Boolean(daily.closing) || Number(daily.summary.unbilled_count) > 0;
    $("#close-day-button").title = Number(daily.summary.unbilled_count) > 0
        ? "미청구 진료를 먼저 처리하세요."
        : "";
    $$('[data-today-checkin]').forEach((button) => button.addEventListener("click", async (event) => {
        event.stopPropagation();
        button.disabled = true;
        try {
            const checked = await api(`/api/appointments/${button.dataset.todayCheckin}/check-in`, {method: "POST", body: {}});
            navigateView("patients");
            await selectPatient(button.dataset.patientId);
            await openEncounter(checked.encounter.id);
            toast("접수하고 진료를 시작했습니다.");
        } catch (error) { handleError(error); } finally { button.disabled = false; }
    }));
    $$('[data-today-encounter]').forEach((node) => node.addEventListener("click", async () => {
        navigateView("patients");
        await selectPatient(node.dataset.patient);
        await openEncounter(node.dataset.todayEncounter);
    }));
    $$('[data-today-patient]').forEach((node) => node.addEventListener("click", async () => {
        navigateView("patients");
        await selectPatient(node.dataset.todayPatient);
    }));
}

$("#close-day-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (!window.confirm("오늘 청구·결제·환불 내역을 확인하고 일마감하시겠습니까? 마감 후 해당 날짜에는 결제·환불을 추가할 수 없습니다.")) return;
    button.disabled = true;
    try {
        const data = await api("/api/billing/daily-close", {method: "POST", body: {date: dateToISO(new Date())}});
        $("#daily-close-status").textContent = `마감 완료 · 청구 ${Number(data.summary.billed_amount).toLocaleString("ko-KR")}원 · 순결제 ${Number(data.summary.net).toLocaleString("ko-KR")}원 · 차이 ${Number(data.summary.billing_payment_difference).toLocaleString("ko-KR")}원`;
        toast("오늘 결제·환불 일마감을 저장했습니다.");
    } catch (error) { handleError(error); button.disabled = false; }
});

$("#task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button", form);
    const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
    const payload = formObject(form);
    payload.patient_id = payload.patient_id || null;
    payload.due_at = now.toISOString().slice(0, 16);
    button.disabled = true;
    try {
        await api("/api/tasks", {method: "POST", body: payload});
        form.elements.title.value = "";
        await loadTodayDashboard();
        toast("할 일을 추가했습니다.");
    } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$("#today-tasks").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-task-done]");
    if (!button) return;
    button.disabled = true;
    try {
        await api(`/api/tasks/${button.dataset.taskDone}`, {method: "PATCH", body: {status: "done"}});
        await loadTodayDashboard();
        toast("할 일을 완료했습니다.");
    } catch (error) { handleError(error); } finally { button.disabled = false; }
});

async function loadPatients(query = $("#patient-search").value) {
    const params = new URLSearchParams();
    const normalizedQuery = String(query || "").trim();
    if (normalizedQuery) params.set("q", normalizedQuery);
    if ($("#show-archived-patients").checked) params.set("status", "all");
    const suffix = params.toString();
    const data = await api(`/api/patients${suffix ? `?${suffix}` : ""}`);
    state.patients = data.items;
    renderPatients();
    renderAppointmentPatientOptions();
}

function renderPatients() {
    const root = $("#patient-list");
    root.innerHTML = state.patients.length ? state.patients.map((patient) => `
        <div class="patient-item ${state.patient?.id === patient.id ? "active" : ""} ${patient.is_archived ? "archived" : ""}" data-patient="${patient.id}">
            <span class="patient-list-chart">차트번호 ${escapeHtml(patient.chart_number)}</span>
            <strong>${escapeHtml(patient.name)}</strong>
            <span>${escapeHtml(speciesLabel(patient.species))} · 진료 ${patient.encounter_count}회${patient.is_archived ? " · 보관됨" : ""}</span>
        </div>
    `).join("") : `<p class="muted small">표시할 환자가 없습니다.</p>`;
    $$("[data-patient]", root).forEach((node) => node.addEventListener("click", () => selectPatient(node.dataset.patient)));
}

let searchTimer;
$("#patient-search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadPatients(event.target.value).catch(handleError), 250);
});
$("#show-archived-patients").addEventListener("change", (event) => {
    if (!event.currentTarget.checked && state.patient?.is_archived) {
        state.patient = null;
        state.encounter = null;
        state.soap = null;
        $("#patient-workspace").classList.add("hidden");
        $("#empty-state").classList.remove("hidden");
        $("#patient-browser").classList.remove("hidden");
        syncHistoryPanel();
    }
    loadPatients().catch(handleError);
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
    form.elements.name.value = state.patient.name || "";
    form.elements.owner_name.value = state.patient.owner_name || "";
    form.elements.owner_phone.value = state.patient.owner_phone || "";
    form.elements.species.value = state.patient.species || "Canine";
    form.elements.breed.value = state.patient.breed || "";
    form.elements.sex_status.value = `${state.patient.sex || "unknown"}:${Boolean(state.patient.neutered)}`;
    form.elements.birth_date.value = state.patient.birth_date || "";
    form.elements.weight_kg.value = state.patient.weight_kg || "";
    form.elements.notes.value = state.patient.notes || "";
    form.elements.current_medications.value = state.patient.current_medications || "";
    form.elements.allergies.value = state.patient.allergies || "";
    form.elements.preventive_care.value = state.patient.preventive_care || "";
    form.classList.remove("hidden");
    form.elements.name.focus();
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
        const payload = formObject(form);
        const [sex, neutered] = payload.sex_status.split(":");
        delete payload.sex_status;
        payload.sex = sex;
        payload.neutered = neutered === "true";
        payload.birth_date = payload.birth_date || null;
        payload.weight_kg = payload.weight_kg ? Number(payload.weight_kg) : null;
        await api(`/api/patients/${patientId}`, {
            method: "PATCH",
            body: payload,
        });
        form.classList.add("hidden");
        await loadPatients();
        await selectPatient(patientId);
        toast("환자 정보를 수정했습니다.");
    } catch (error) { handleError(error); }
});

$("#archive-patient-button").addEventListener("click", async (event) => {
    if (!state.patient) return;
    const button = event.currentTarget;
    const patientId = state.patient.id;
    const archive = !Boolean(state.patient.is_archived);
    const message = archive
        ? "이 환자를 기본 목록에서 숨길까요? 진료 기록은 삭제되지 않습니다."
        : "이 환자를 기본 목록에 다시 표시할까요?";
    if (!confirm(message)) return;

    button.disabled = true;
    try {
        await api(`/api/patients/${patientId}`, {
            method: "PATCH",
            body: {is_archived: archive},
        });
        if (archive && !$("#show-archived-patients").checked) {
            state.patient = null;
            state.encounter = null;
            state.soap = null;
            $("#patient-workspace").classList.add("hidden");
            $("#empty-state").classList.remove("hidden");
            $("#patient-browser").classList.remove("hidden");
            syncHistoryPanel();
            await loadPatients();
        } else {
            await loadPatients();
            await selectPatient(patientId);
        }
        toast(archive ? "환자를 보관 목록으로 이동했습니다." : "환자를 기본 목록에 복원했습니다.");
    } catch (error) {
        handleError(error);
    } finally {
        button.disabled = false;
    }
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
    const [patient, trends, monitoring] = await Promise.all([
        api(`/api/patients/${patientId}`),
        api(`/api/patients/${patientId}/cardio-trends`),
        api(`/api/patients/${patientId}/monitoring`),
    ]);
    state.patient = patient;
    state.trends = trends.items;
    state.monitoring = monitoring.items;
    state.revisions = [];
    state.encounter = null;
    state.soap = null;
    state.cardiacComparison = null;
    state.workflow = {ecg: null, lab: null, prescription: null, billing: null, echoVideos: []};
    state.commercial = {workflow: null, diagnosticPlan: [], education: null, prescription: null, invoice: null};
    $("#patient-browser").classList.add("hidden");
    $("#empty-state").classList.add("hidden");
    $("#patient-workspace").classList.remove("hidden");
    const encounterForm = $("#encounter-form");
    encounterForm.reset();
    encounterForm.classList.add("hidden");
    $("#patient-contact-form").classList.add("hidden");
    $("#encounter-workspace").classList.add("hidden");
    $("#encounter-placeholder").classList.remove("hidden");
    $("#patient-chart").textContent = `차트번호 ${state.patient.chart_number}`;
    $("#patient-title").textContent = state.patient.name;
    const archived = Boolean(state.patient.is_archived);
    $("#patient-archive-status").classList.toggle("hidden", !archived);
    const archiveButton = $("#archive-patient-button");
    archiveButton.textContent = archived ? "환자 복원" : "목록에서 숨기기";
    archiveButton.className = archived ? "secondary" : "danger-ghost";
    const encounterButton = $("#new-encounter-button");
    encounterButton.disabled = archived;
    encounterButton.title = archived ? "환자를 복원한 뒤 새 진료를 작성할 수 있습니다." : "";
    $("#patient-meta").textContent = [
        speciesLabel(state.patient.species), state.patient.breed,
        sexLabel(state.patient.sex, state.patient.neutered),
        formatPatientAge(state.patient.birth_date),
        state.patient.owner_name ? `보호자 ${state.patient.owner_name}` : null,
        state.patient.owner_phone,
        state.patient.weight_kg ? `${state.patient.weight_kg} kg` : null,
    ].filter(Boolean).join(" · ");
    renderPatients();
    renderPatientSummary();
    renderPatientCardiacBadges();
    syncHistoryPanel();
    renderEncounters();
    renderCardioSummary();
    renderTrends();
    renderDiagnostics();
}

$("#back-to-patient-list").addEventListener("click", () => {
    $("#patient-workspace").classList.add("hidden");
    $("#empty-state").classList.remove("hidden");
    $("#patient-browser").classList.remove("hidden");
});

function renderPatientSummary() {
    if (!state.patient) return;
    const items = [
        ["종", speciesLabel(state.patient.species)],
        ["품종", state.patient.breed || "미입력"],
        ["성별", sexLabel(state.patient.sex, state.patient.neutered)],
        ["나이", formatPatientAge(state.patient.birth_date)?.replace("나이: ", "") || "미입력"],
        ["보호자", state.patient.owner_name || "미입력"],
        ["연락처", state.patient.owner_phone || "미입력"],
    ];
    $("#patient-summary-list").innerHTML = items.map(([label, value]) => `
        <dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>
    `).join("");
    $("#patient-past-history").textContent = state.patient.notes || "미입력";
    $("#patient-medications").textContent = state.patient.current_medications || "미입력";
    $("#patient-allergies").textContent = state.patient.allergies || "미입력";
    $("#patient-preventive-care").textContent = state.patient.preventive_care || "미입력";
}

function renderPatientCardiacBadges() {
    const latest = state.trends.at(-1) || {};
    const diagnosis = state.patient?.encounters?.[0]?.assessment_diagnosis_name;
    const badges = [
        diagnosis,
        latest.acvim_stage ? `ACVIM ${latest.acvim_stage}` : null,
        latest.murmur_grade ? `심잡음 ${latest.murmur_grade}` : null,
        latest.chf_status === "present" ? "CHF 있음" : latest.chf_status === "none" ? "CHF 없음" : null,
    ].filter(Boolean);
    $("#patient-cardiac-badges").innerHTML = badges.map((item) => `<span>${escapeHtml(item)}</span>`).join("");
}

function syncHistoryPanel() {
    const hasPatient = Boolean(state.patient);
    $("#history-empty").classList.toggle("hidden", hasPatient);
    $("#history-content").classList.toggle("hidden", !hasPatient);
    $("#history-title").textContent = hasPatient
        ? `${state.patient.name} 진료 History`
        : "환자 진료 History";
    $("#history-patient-name").textContent = hasPatient
        ? `${state.patient.name} · 차트번호 ${state.patient.chart_number}`
        : "";
}

function renderEncounters() {
    const root = $("#encounter-list");
    const encounters = state.patient?.encounters || [];
    root.innerHTML = encounters.length ? encounters.map((encounter) => {
        const diagnosisName = encounter.assessment_diagnosis_name || "진단명 미입력";
        return `
        <div class="encounter-card ${state.encounter?.id === encounter.id ? "active" : ""}" data-encounter="${encounter.id}">
            <div>
                <div class="encounter-history-title"><time>${escapeHtml(formatDate(encounter.visit_at))}</time><span aria-hidden="true">·</span><strong title="${escapeHtml(diagnosisName)}">${escapeHtml(diagnosisName)}</strong></div>
                <div class="muted small">${escapeHtml(encounter.chief_complaint || "주호소 없음")}</div>
            </div>
            <span class="status ${encounter.workflow_stage === "closed" ? "completed" : "draft"}">${encounter.workflow_stage === "closed" ? "진료 종료" : escapeHtml(workflowLabels[encounter.workflow_stage] || "진행 중")}</span>
        </div>
    `;
    }).join("") : `<p class="muted small">진료 이력이 없습니다. 새 진료를 생성하세요.</p>`;
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
        if (state.user?.role === "veterinarian") {
            toast("새 진료를 생성했습니다.");
            try {
                await generateSoapStage("S");
            } catch (error) {
                handleError(error);
                renderSoap();
            }
        } else {
            toast("접수가 완료되었습니다. 수의사가 S 기록을 작성할 수 있습니다.");
        }
    } catch (error) {
        handleError(error);
    } finally {
        button.disabled = false;
        button.textContent = "진료 시작";
    }
});

async function openEncounter(encounterId) {
    const [encounter, soap, cardiacComparison, trends, monitoring, revisions, ecg, lab, prescription, billing, echoVideos, workflow, diagnosticPlan, education, structuredPrescription, invoice] = await Promise.all([
        api(`/api/encounters/${encounterId}`),
        api(`/api/encounters/${encounterId}/soap`),
        api(`/api/encounters/${encounterId}/cardiac-exam`),
        api(`/api/patients/${state.patient.id}/cardio-trends`),
        api(`/api/patients/${state.patient.id}/monitoring`),
        api(`/api/encounters/${encounterId}/clinical-revisions`),
        api(`/api/encounters/${encounterId}/ecg`),
        api(`/api/encounters/${encounterId}/lab`),
        api(`/api/encounters/${encounterId}/prescription`),
        api(`/api/encounters/${encounterId}/billing`),
        api(`/api/encounters/${encounterId}/echo-videos`),
        api(`/api/encounters/${encounterId}/workflow`),
        api(`/api/encounters/${encounterId}/diagnostic-plan`),
        api(`/api/encounters/${encounterId}/education`),
        api(`/api/encounters/${encounterId}/structured-prescription`),
        api(`/api/encounters/${encounterId}/invoice`),
    ]);
    state.encounter = encounter;
    state.soap = soap;
    state.cardiacComparison = cardiacComparison;
    state.trends = trends.items;
    state.monitoring = monitoring.items;
    state.revisions = revisions.items;
    state.workflow = {
        ecg: ecg.exam,
        lab: lab.result,
        prescription: prescription.prescription,
        billing: billing.billing,
        echoVideos: echoVideos.items,
    };
    state.commercial = {
        workflow,
        diagnosticPlan: diagnosticPlan.items,
        education: education.document,
        prescription: structuredPrescription.prescription,
        invoice: invoice.invoice,
    };
    state.selectedCandidates = {};
    $("#encounter-form").classList.add("hidden");
    $("#encounter-placeholder").classList.add("hidden");
    $("#encounter-workspace").classList.remove("hidden");
    $("#active-encounter-label").textContent = `${formatDate(encounter.visit_at, true)} · ${encounter.disease_name || "미분류 심장 진료"} · ${encounter.chief_complaint || "주호소 없음"}`;
    reflectEncounterStatus();
    renderEncounters();
    renderSoap();
    populateEncounterEditForm();
    renderWorkflow();
    renderCardioSummary();
    renderPatientCardiacBadges();
    renderDiagnostics();
    renderTrends();
    $("#encounter-workspace").scrollIntoView({behavior: "smooth", block: "start"});
}

function reflectEncounterStatus() {
    if (!state.encounter) return;
    const cached = state.patient?.encounters?.find((item) => item.id === state.encounter.id);
    if (cached) {
        cached.status = state.encounter.status;
        cached.workflow_stage = state.encounter.workflow_stage;
        cached.assessment_diagnosis_name = state.encounter.assessment_diagnosis_name;
    }
}

const workflowLabels = {
    intake: "접수·문진", examination: "신체검사", diagnostics: "검사",
    documentation: "SOAP", education: "보호자 설명", checkout: "수납", closed: "종료",
};

function populateEncounterEditForm() {
    if (!state.encounter) return;
    const form = $("#encounter-edit-form");
    form.elements.disease_id.value = state.encounter.disease_id || "";
    form.elements.visit_at.value = datetimeLocalValue(state.encounter.visit_at);
    form.elements.chief_complaint.value = state.encounter.chief_complaint || "";
    form.elements.history_text.value = state.encounter.history_text || "";
    form.elements.physical_exam.value = state.encounter.physical_exam || "";
}

function renderWorkflow() {
    const workflow = state.commercial.workflow;
    if (!workflow) return;
    const currentIndex = workflow.stages.indexOf(workflow.stage);
    const veterinarianRequired = ["diagnostics", "documentation", "education"].includes(workflow.next_stage);
    const roleBlocked = state.user?.role === "staff" && veterinarianRequired;
    const closed = workflow.stage === "closed";
    const canReopen = closed && state.user?.role === "veterinarian";
    $("#workflow-current").textContent = workflowLabels[workflow.stage] || workflow.stage;
    $("#workflow-steps").innerHTML = workflow.stages.map((stage, index) => `
        <span class="workflow-step ${index < currentIndex ? "done" : ""} ${index === currentIndex ? "current" : ""}">${escapeHtml(workflowLabels[stage] || stage)}</span>
    `).join("");
    const visibleBlockers = roleBlocked
        ? [...workflow.blockers, {message: "다음 임상 단계는 수의사 확인이 필요합니다."}]
        : workflow.blockers;
    $("#workflow-blockers").innerHTML = visibleBlockers.length
        ? visibleBlockers.map((item) => `<p>${escapeHtml(item.message)}</p>`).join("")
        : '<span class="muted small">다음 단계로 진행할 수 있습니다.</span>';
    const button = $("#workflow-next-button");
    button.classList.toggle("hidden", !workflow.next_stage && !canReopen);
    button.dataset.workflowAction = canReopen ? "reopen" : "advance";
    button.textContent = canReopen
        ? "진료 다시 열기"
        : (workflow.next_stage ? `${workflowLabels[workflow.next_stage]} 단계로 진행` : "진료 종료됨");
    button.disabled = canReopen ? false : Boolean(workflow.blockers.length) || roleBlocked || !workflow.next_stage;
    $("#complete-visit-button").disabled = workflow.stage !== "checkout" || Boolean(workflow.blockers.length);
    $("#complete-visit-button").title = workflow.blockers[0]?.message || "";
    $$("input, select, textarea, button", $("#encounter-edit-form")).forEach((control) => {
        control.disabled = closed;
    });
    $("#encounter-edit-form").elements.disease_id.disabled = closed || state.user?.role === "staff";
    $("#encounter-edit-form").elements.physical_exam.disabled = closed || state.user?.role === "staff";
    $("#save-visit-button").disabled = closed || state.user?.role !== "veterinarian";
}

async function refreshCommercialState() {
    if (!state.encounter) return;
    const [workflow, diagnosticPlan, education, prescription, invoice] = await Promise.all([
        api(`/api/encounters/${state.encounter.id}/workflow`),
        api(`/api/encounters/${state.encounter.id}/diagnostic-plan`),
        api(`/api/encounters/${state.encounter.id}/education`),
        api(`/api/encounters/${state.encounter.id}/structured-prescription`),
        api(`/api/encounters/${state.encounter.id}/invoice`),
    ]);
    state.commercial = {
        workflow,
        diagnosticPlan: diagnosticPlan.items,
        education: education.document,
        prescription: prescription.prescription,
        invoice: invoice.invoice,
    };
    renderWorkflow();
    renderDiagnosticPlan();
}

$("#workflow-next-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        const reopening = button.dataset.workflowAction === "reopen";
        state.commercial.workflow = await api(`/api/encounters/${state.encounter.id}/workflow/transition`, {
            method: "POST",
            body: reopening ? {action: "reopen", target_stage: "documentation"} : {},
        });
        state.encounter.workflow_stage = state.commercial.workflow.stage;
        reflectEncounterStatus();
        renderEncounters();
        renderWorkflow();
        await loadTodayDashboard();
        if (reopening) {
            await refreshCommercialState();
            renderSoap();
            toast("진료를 SOAP 단계로 다시 열었습니다.");
        } else {
            toast(`${workflowLabels[state.commercial.workflow.stage]} 단계로 진행했습니다.`);
            if (state.commercial.workflow.stage === "diagnostics") navigateView("diagnostics");
            if (state.commercial.workflow.stage === "education") $('[data-report="prescription"]')?.click();
            if (state.commercial.workflow.stage === "checkout") $('[data-report="billing"]')?.click();
        }
    } catch (error) { handleError(error); await refreshCommercialState(); }
    finally { renderWorkflow(); }
});

$("#encounter-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    try {
        const payload = formObject(form);
        if (state.user?.role === "staff") {
            delete payload.disease_id;
            delete payload.physical_exam;
        }
        state.encounter = await api(`/api/encounters/${state.encounter.id}`, {method: "PATCH", body: payload});
        state.soap = await api(`/api/encounters/${state.encounter.id}/soap`);
        reflectEncounterStatus();
        renderSoap();
        await refreshCommercialState();
        toast("진료 기본정보를 저장했습니다.");
    } catch (error) { handleError(error); } finally {
        button.disabled = state.commercial.workflow?.stage === "closed";
    }
});

const cardiacBooleanFields = [
    "cough", "syncope", "exercise_intolerance", "nocturnal_tachypnea",
    "sam", "lvoto", "sec_present", "la_thrombus", "pericardial_effusion",
];
const cardiacNumberFields = [
    "body_weight_kg", "heart_rate_bpm", "respiratory_rate_rpm",
    "systolic_bp_mmhg", "vhs", "vlas", "home_rr_rpm", "la_ao",
    "lvidd_cm", "lviddn", "lvids_cm", "fs_percent", "e_velocity_ms",
    "a_velocity_ms", "tr_vmax_ms", "pr_vmax_ms", "follow_up_months",
];
const cardioMetricDefinitions = [
    ["body_weight_kg", "BW", "kg"],
    ["heart_rate_bpm", "HR", ""],
    ["systolic_bp_mmhg", "BP", ""],
    ["vhs", "VHS", ""],
    ["vlas", "VLAS", ""],
    ["la_ao", "LA/Ao", ""],
    ["lviddn", "LVIDDN", ""],
    ["e_velocity_ms", "E velocity", ""],
    ["tr_vmax_ms", "TR Vmax", ""],
    ["home_rr_rpm", "Home RR", ""],
];

function formatMetric(value, unit = "") {
    if (value === null || value === undefined || value === "") return "–";
    return `${Number(value).toLocaleString("ko-KR", {maximumFractionDigits: 3})}${unit ? ` ${unit}` : ""}`;
}

function renderCardioSummary() {
    const comparison = state.cardiacComparison || {exam: null, previous: null, deltas: {}};
    const current = comparison.exam || {};
    const previous = comparison.previous || {};
    $("#cardio-summary-table").innerHTML = `
        <div class="summary-metric summary-table-head"><span>항목</span><span>현재</span><span>이전</span><span>변화</span></div>
        ${cardioMetricDefinitions.map(([key, label, unit]) => {
        const delta = comparison.deltas?.[key];
        const alert = delta !== null && delta !== undefined && Math.abs(delta) > 0;
        return `<div class="summary-metric"><span>${label}</span><span class="metric-current">${formatMetric(current[key], unit)}</span><span>${formatMetric(previous[key], unit)}</span><span class="metric-delta ${alert ? "alert" : ""}">${delta === null || delta === undefined ? "–" : `${delta > 0 ? "+" : ""}${formatMetric(delta)}`}</span></div>`;
    }).join("")}`;
    const stage = current.acvim_stage || "미확정";
    const ph = {low: "낮음", intermediate: "중간", high: "높음"}[current.ph_risk] || "미입력";
    const chf = {none: "없음", suspected: "의심", present: "있음"}[current.chf_status] || "미입력";
    $("#cardio-classification").innerHTML = `
        <div class="classification-row"><span>ACVIM</span><strong>${escapeHtml(stage)}</strong></div>
        <div class="classification-row"><span>PH 위험</span><strong>${escapeHtml(ph)}</strong></div>
        <div class="classification-row"><span>CHF</span><strong>${escapeHtml(chf)}</strong></div>
        <div class="classification-row"><span>다음검사</span><strong>${current.follow_up_months ? `${current.follow_up_months}개월` : "미정"}</strong></div>
    `;
    const alerts = [];
    [["la_ao", "LA/Ao"], ["vhs", "VHS"], ["home_rr_rpm", "Home RR"], ["lviddn", "LVIDDN"]].forEach(([key, label]) => {
        const delta = comparison.deltas?.[key];
        if (delta !== null && delta !== undefined && delta > 0) alerts.push(`${label} ${delta > 0 ? "+" : ""}${formatMetric(delta)}`);
    });
    $("#cardio-change-alerts").innerHTML = alerts.length
        ? `<strong class="small">⚠ 변화</strong>${alerts.map((item) => `<div class="change-alert">${escapeHtml(item)}</div>`).join("")}`
        : '<p class="empty-copy">비교 가능한 변화가 없습니다.</p>';
}

function populateCardiacExamForm() {
    const form = $("#cardiac-exam-form");
    if (!form) return;
    form.reset();
    const exam = state.cardiacComparison?.exam || {};
    for (const field of cardiacBooleanFields) {
        if (form.elements[field]) form.elements[field].checked = Boolean(exam[field]);
    }
    for (const [key, value] of Object.entries(exam)) {
        const control = form.elements[key];
        if (!control || cardiacBooleanFields.includes(key) || key === "mr_severity") continue;
        control.value = value ?? "";
    }
    if (exam.mr_severity) {
        const radio = form.querySelector(`[name="mr_severity"][value="${exam.mr_severity}"]`);
        if (radio) radio.checked = true;
    }
    updateEchoCalculation();
}

function datetimeLocalValue(value) {
    if (!value) return "";
    return String(value).replace(" ", "T").slice(0, 16);
}

function populateWorkflowForms() {
    const form = $("#cardiac-exam-form");
    if (!form) return;
    const ecg = state.workflow.ecg || {};
    const lab = state.workflow.lab || {};
    const values = {
        ecg_recorded_at: datetimeLocalValue(ecg.recorded_at),
        ecg_heart_rate_bpm: ecg.heart_rate_bpm,
        ecg_rhythm: ecg.rhythm,
        ecg_pr_ms: ecg.pr_ms,
        ecg_qrs_ms: ecg.qrs_ms,
        ecg_qt_ms: ecg.qt_ms,
        ecg_interpretation: ecg.interpretation,
        lab_collected_at: datetimeLocalValue(lab.collected_at),
        nt_probnp_pmol_l: lab.nt_probnp_pmol_l,
        troponin_i_ng_ml: lab.troponin_i_ng_ml,
        bun_mg_dl: lab.bun_mg_dl,
        creatinine_mg_dl: lab.creatinine_mg_dl,
        sodium_mmol_l: lab.sodium_mmol_l,
        potassium_mmol_l: lab.potassium_mmol_l,
        lab_notes: lab.notes,
    };
    for (const [name, value] of Object.entries(values)) {
        if (form.elements[name]) form.elements[name].value = value ?? "";
    }
    $("#echo-video-recorded-at").value = datetimeLocalValue(state.encounter?.visit_at);
    renderEchoVideos();
}

function nullableNumber(control) {
    return control?.value === "" || control?.value === undefined ? null : Number(control.value);
}

async function refreshAfterObjectiveChange(message) {
    const [soap, encounter, revisions] = await Promise.all([
        api(`/api/encounters/${state.encounter.id}/soap`),
        api(`/api/encounters/${state.encounter.id}`),
        api(`/api/encounters/${state.encounter.id}/clinical-revisions`),
    ]);
    state.soap = soap;
    state.encounter = encounter;
    state.revisions = revisions.items;
    renderSoap();
    reflectEncounterStatus();
    renderEncounters();
    await refreshCommercialState();
    await loadTodayDashboard();
    toast(message);
}

async function saveEcg() {
    if (!state.encounter) return toast("먼저 진료를 선택하세요.", "error");
    const form = $("#cardiac-exam-form");
    const payload = {
        recorded_at: form.elements.ecg_recorded_at.value || null,
        heart_rate_bpm: nullableNumber(form.elements.ecg_heart_rate_bpm),
        rhythm: form.elements.ecg_rhythm.value.trim() || null,
        pr_ms: nullableNumber(form.elements.ecg_pr_ms),
        qrs_ms: nullableNumber(form.elements.ecg_qrs_ms),
        qt_ms: nullableNumber(form.elements.ecg_qt_ms),
        interpretation: form.elements.ecg_interpretation.value.trim() || null,
    };
    const data = await api(`/api/encounters/${state.encounter.id}/ecg`, {method: "PUT", body: payload});
    state.workflow.ecg = data.exam;
    await refreshAfterObjectiveChange("ECG 기록을 저장했습니다. O 이후 SOAP를 다시 확인하세요.");
}

async function saveLab() {
    if (!state.encounter) return toast("먼저 진료를 선택하세요.", "error");
    const form = $("#cardiac-exam-form");
    const numberFields = ["nt_probnp_pmol_l", "troponin_i_ng_ml", "bun_mg_dl", "creatinine_mg_dl", "sodium_mmol_l", "potassium_mmol_l"];
    const payload = {
        collected_at: form.elements.lab_collected_at.value || null,
        notes: form.elements.lab_notes.value.trim() || null,
    };
    for (const field of numberFields) payload[field] = nullableNumber(form.elements[field]);
    const data = await api(`/api/encounters/${state.encounter.id}/lab`, {method: "PUT", body: payload});
    state.workflow.lab = data.result;
    await refreshAfterObjectiveChange("Lab 기록을 저장했습니다. O 이후 SOAP를 다시 확인하세요.");
}

function renderEchoVideos() {
    const root = $("#echo-video-list");
    if (!root) return;
    const videos = state.workflow.echoVideos || [];
    root.innerHTML = videos.length ? videos.map((item) => `
        <article class="video-card">
            <video controls preload="metadata" src="${escapeHtml(item.file_url)}"></video>
            <strong>${escapeHtml(item.note || item.original_name)}</strong>
            <span>${escapeHtml(formatDate(item.recorded_at || item.created_at, true))} · ${(Number(item.size_bytes) / 1024 / 1024).toFixed(1)} MB</span>
        </article>
    `).join("") : '<p class="empty-copy">등록된 Echo 영상이 없습니다.</p>';
}

async function uploadEchoVideo() {
    if (!state.encounter) return toast("먼저 진료를 선택하세요.", "error");
    const input = $("#echo-video-file");
    if (!input.files.length) return toast("MP4 또는 WebM 영상을 선택하세요.", "error");
    const body = new FormData();
    body.append("file", input.files[0]);
    body.append("recorded_at", $("#echo-video-recorded-at").value || "");
    body.append("note", $("#echo-video-note").value.trim());
    const video = await api(`/api/encounters/${state.encounter.id}/echo-videos`, {method: "POST", body});
    state.workflow.echoVideos.unshift(video);
    input.value = "";
    $("#echo-video-note").value = "";
    renderEchoVideos();
    toast("Echo 영상을 등록했습니다.");
}

function cardiacFormPayload() {
    const form = $("#cardiac-exam-form");
    const payload = {};
    for (const field of cardiacBooleanFields) payload[field] = Boolean(form.elements[field]?.checked);
    for (const field of cardiacNumberFields) {
        const value = form.elements[field]?.value;
        payload[field] = value === "" || value === undefined ? null : Number(value);
    }
    for (const field of ["rhythm", "findings", "assessment", "murmur_grade", "acvim_stage", "ph_risk", "chf_status"]) {
        payload[field] = form.elements[field]?.value?.trim() || null;
    }
    payload.mr_severity = form.querySelector('[name="mr_severity"]:checked')?.value || null;
    return payload;
}

function updateEchoCalculation() {
    const form = $("#cardiac-exam-form");
    if (!form) return;
    const e = Number(form.elements.e_velocity_ms.value);
    const a = Number(form.elements.a_velocity_ms.value);
    form.elements.e_a_ratio.value = e > 0 && a > 0 ? (e / a).toFixed(3) : "";
    const laAo = Number(form.elements.la_ao.value);
    const lviddn = Number(form.elements.lviddn.value);
    const tr = Number(form.elements.tr_vmax_ms.value);
    const notes = [];
    if (laAo >= 1.6) notes.push("⚠ LA enlargement 가능성");
    if (lviddn >= 1.7) notes.push("⚠ LV enlargement 가능성");
    if (tr >= 3.4) notes.push("⚠ PH 위험 평가 필요");
    if (laAo >= 1.6 && lviddn >= 1.7) notes.push("예상 분류 참고: MMVD / ACVIM B2 가능성");
    $("#auto-assessment").innerHTML = `<strong>자동평가 · 참고용</strong><p>${notes.length ? notes.join("<br>") : "측정값을 입력하면 변화 경고와 예상 분류 참고 문구가 표시됩니다."}</p>`;
}

function activateEchoSubtab(tab) {
    $$('[data-echo-subtab]').forEach((button) => button.classList.toggle("active", button.dataset.echoSubtab === tab));
    const target = {
        "2d": '[name="la_ao"]',
        mmode: '[name="lvidd_cm"]',
        doppler: '[name="e_velocity_ms"]',
        tdi: '[name="assessment"]',
        findings: '[name="findings"]',
    }[tab];
    const control = target ? $(target, $("#cardiac-exam-form")) : null;
    if (control) {
        control.scrollIntoView({behavior: "smooth", block: "center"});
        control.focus({preventScroll: true});
    }
}

async function saveCardiacExam() {
    if (!state.encounter) return toast("먼저 진료를 선택하세요.", "error");
    state.cardiacComparison = await api(`/api/encounters/${state.encounter.id}/cardiac-exam`, {
        method: "PUT",
        body: cardiacFormPayload(),
    });
    const [soap, encounter, trends, revisions] = await Promise.all([
        api(`/api/encounters/${state.encounter.id}/soap`),
        api(`/api/encounters/${state.encounter.id}`),
        api(`/api/patients/${state.patient.id}/cardio-trends`),
        api(`/api/encounters/${state.encounter.id}/clinical-revisions`),
    ]);
    state.soap = soap;
    state.encounter = encounter;
    state.trends = trends.items;
    state.revisions = revisions.items;
    if (state.cardiacComparison?.exam?.body_weight_kg) {
        state.patient.weight_kg = state.cardiacComparison.exam.body_weight_kg;
        renderPatientSummary();
    }
    renderSoap();
    renderCardioSummary();
    renderPatientCardiacBadges();
    renderTrends();
    populateCardiacExamForm();
    populateWorkflowForms();
    await refreshCommercialState();
    await loadTodayDashboard();
    toast("심장검사를 저장했습니다. O 이후 SOAP를 다시 확인하세요.");
}

function setDiagnosticTab(tab) {
    $$('[data-diagnostic-tab]').forEach((button) => button.classList.toggle("active", button.dataset.diagnosticTab === tab));
    $$('[data-diagnostic-panel]').forEach((panel) => panel.classList.toggle("hidden", panel.dataset.diagnosticPanel !== tab));
}

const diagnosticLabels = {echo: "Echo", ecg: "ECG", xray: "X-ray", bp: "BP·Vitals", lab: "Lab"};
const diagnosticStatusLabels = {planned: "계획", completed: "결과 입력", reviewed: "수의사 검토", not_required: "미실시"};

function renderDiagnosticPlan() {
    const root = $("#diagnostic-plan-items");
    if (!root) return;
    const byType = Object.fromEntries((state.commercial.diagnosticPlan || []).map((item) => [item.exam_type, item]));
    const isVeterinarian = state.user?.role === "veterinarian";
    const closed = state.commercial.workflow?.stage === "closed";
    const protectedStatus = (state.commercial.diagnosticPlan || []).some((item) => ["reviewed", "not_required"].includes(item.status));
    root.innerHTML = Object.keys(diagnosticLabels).map((type) => {
        const item = byType[type] || {};
        const locked = closed || (!isVeterinarian && ["reviewed", "not_required"].includes(item.status));
        return `<label class="diagnostic-plan-item"><strong>${diagnosticLabels[type]}</strong><select data-plan-status="${type}" ${locked ? "disabled" : ""}><option value="">선택 안함</option>${Object.entries(diagnosticStatusLabels).map(([value, label]) => `<option value="${value}" ${item.status === value ? "selected" : ""} ${!isVeterinarian && ["reviewed", "not_required"].includes(value) ? "disabled" : ""}>${label}</option>`).join("")}</select><input data-plan-reason="${type}" maxlength="500" placeholder="미실시 사유" value="${escapeHtml(item.reason || "")}" ${locked ? "disabled" : ""}></label>`;
    }).join("");
    const saveButton = $("#save-diagnostic-plan");
    saveButton.disabled = closed || (!isVeterinarian && protectedStatus);
    saveButton.title = closed
        ? "종료된 진료는 다시 연 뒤 수정할 수 있습니다."
        : (saveButton.disabled ? "수의사가 검토한 검사 계획은 수의사만 변경할 수 있습니다." : "");
}

$("#save-diagnostic-plan").addEventListener("click", async (event) => {
    if (!state.encounter) return toast("진료를 먼저 선택하세요.", "error");
    const button = event.currentTarget;
    const items = Object.keys(diagnosticLabels).map((type) => ({
        exam_type: type,
        status: $(`[data-plan-status="${type}"]`).value,
        reason: $(`[data-plan-reason="${type}"]`).value.trim() || null,
    })).filter((item) => item.status);
    button.disabled = true;
    try {
        const data = await api(`/api/encounters/${state.encounter.id}/diagnostic-plan`, {method: "PUT", body: {items}});
        state.commercial.diagnosticPlan = data.items;
        renderDiagnosticPlan();
        state.commercial.workflow = await api(`/api/encounters/${state.encounter.id}/workflow`);
        renderWorkflow();
        toast("검사 계획과 검토 상태를 저장했습니다.");
    } catch (error) { handleError(error); } finally { renderDiagnosticPlan(); }
});

function renderDiagnostics() {
    const selected = Boolean(state.patient && state.encounter);
    $("#diagnostics-no-selection").classList.toggle("hidden", selected);
    $("#diagnostics-workspace").classList.toggle("hidden", !selected);
    $("#diagnostics-context").textContent = selected
        ? `${state.patient.name} · ${formatDate(state.encounter.visit_at, true)} · ${state.encounter.disease_name || "미분류 심장 진료"}`
        : "환자와 진료를 먼저 선택하세요.";
    if (!selected) return;
    populateCardiacExamForm();
    populateWorkflowForms();
    renderDiagnosticPlan();
    const xrayRoot = $("#diagnostic-xray-root");
    xrayRoot.innerHTML = xrayPanelHtml();
    const details = $(".objective-xrays", xrayRoot);
    if (details) details.open = true;
    bindXrayControls(xrayRoot);
    const closed = state.commercial.workflow?.stage === "closed";
    const clinicalEdit = state.user?.role === "veterinarian" && !closed;
    $("#cardiac-exam-form button[type=submit]").disabled = !clinicalEdit;
    $("#save-ecg").disabled = !clinicalEdit;
    $("#save-lab").disabled = !clinicalEdit;
    $("#upload-echo-video").disabled = closed;
}

function trendChartHtml(key, label) {
    const points = state.trends
        .filter((item) => item[key] !== null && item[key] !== undefined)
        .map((item) => ({date: new Date(item.visit_at), value: Number(item[key])}));
    if (!points.length) return `<article class="trend-chart"><h3>${label}</h3><p class="empty-copy">기록 없음</p></article>`;
    const width = 520;
    const height = 170;
    const pad = 30;
    const values = points.map((point) => point.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const coords = points.map((point, index) => ({
        ...point,
        x: points.length === 1 ? width / 2 : pad + index * ((width - pad * 2) / (points.length - 1)),
        y: height - pad - ((point.value - min) / range) * (height - pad * 2),
    }));
    const path = coords.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
    return `<article class="trend-chart"><h3>${label}</h3><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${label} 추세"><line class="trend-axis" x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"></line><path class="trend-line" d="${path}"></path>${coords.map((point) => `<circle class="trend-point" cx="${point.x}" cy="${point.y}" r="5"></circle><text class="trend-label" x="${point.x}" y="${point.y - 10}" text-anchor="middle">${formatMetric(point.value)}</text><text class="trend-label" x="${point.x}" y="${height - 10}" text-anchor="middle">${point.date.getFullYear()}.${String(point.date.getMonth() + 1).padStart(2, "0")}</text>`).join("")}</svg></article>`;
}

function monitoringChartHtml(key, label) {
    const points = state.monitoring
        .filter((item) => item[key] !== null && item[key] !== undefined)
        .map((item) => ({date: new Date(item.measured_at), value: Number(item[key])}))
        .reverse();
    if (!points.length) return `<article class="trend-chart"><h3>${label}</h3><p class="empty-copy">기록 없음</p></article>`;
    const width = 520;
    const height = 170;
    const pad = 30;
    const values = points.map((point) => point.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const coords = points.map((point, index) => ({
        ...point,
        x: points.length === 1 ? width / 2 : pad + index * ((width - pad * 2) / (points.length - 1)),
        y: height - pad - ((point.value - min) / range) * (height - pad * 2),
    }));
    const path = coords.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
    return `<article class="trend-chart"><h3>${label}</h3><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${label} 추세"><line class="trend-axis" x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"></line><path class="trend-line" d="${path}"></path>${coords.map((point) => `<circle class="trend-point" cx="${point.x}" cy="${point.y}" r="5"></circle><text class="trend-label" x="${point.x}" y="${point.y - 10}" text-anchor="middle">${formatMetric(point.value)}</text><text class="trend-label" x="${point.x}" y="${height - 10}" text-anchor="middle">${point.date.getMonth() + 1}.${point.date.getDate()}</text>`).join("")}</svg></article>`;
}

function currentDatetimeLocal() {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function renderMonitoring() {
    const form = $("#monitoring-form");
    if (!form || !state.patient) return;
    if (!form.elements.measured_at.value) form.elements.measured_at.value = currentDatetimeLocal();
    $("button[type=submit]", form).disabled = Boolean(state.patient.is_archived);
    const sourceLabels = {clinic: "병원", owner_report: "보호자 보고", device: "장비"};
    const adherenceLabels = {all: "모두 투약", partial: "일부 누락", missed: "투약 못함", unknown: "미확인"};
    $("#monitoring-list").innerHTML = state.monitoring.length ? state.monitoring.map((item) => {
        const symptoms = [item.cough ? "기침" : null, item.dyspnea ? "호흡곤란" : null, item.syncope ? "실신" : null].filter(Boolean);
        const values = [
            item.body_weight_kg !== null ? `BW ${formatMetric(item.body_weight_kg, "kg")}` : null,
            item.home_rr_rpm !== null ? `Home RR ${formatMetric(item.home_rr_rpm)}` : null,
            item.systolic_bp_mmhg !== null ? `BP ${formatMetric(item.systolic_bp_mmhg)}` : null,
            item.heart_rate_bpm !== null ? `HR ${formatMetric(item.heart_rate_bpm)}` : null,
            symptoms.length ? `증상 ${symptoms.join(", ")}` : null,
            `복약 ${adherenceLabels[item.medication_adherence] || "미확인"}`,
        ].filter(Boolean);
        const notes = [item.adverse_effects ? `이상반응: ${item.adverse_effects}` : null, item.notes].filter(Boolean).join("\n");
        return `<article class="monitoring-entry"><header><strong>${escapeHtml(formatDate(item.measured_at, true))}</strong><span class="status">${escapeHtml(sourceLabels[item.source] || item.source)}</span></header><div class="monitoring-values">${values.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</div>${notes ? `<p class="monitoring-note">${escapeHtml(notes)}</p>` : ""}${item.correction_of_id ? '<small class="review-warning">이전 기록의 정정 로그</small>' : ""}</article>`;
    }).join("") : '<p class="empty-copy">등록된 경과 모니터링이 없습니다.</p>';
}

function renderRevisions() {
    const panel = $("#revision-panel");
    if (!panel) return;
    panel.classList.toggle("hidden", !state.encounter);
    if (!state.encounter) return;
    const typeLabels = {cardiac: "심장검사", ecg: "ECG", lab: "Lab", xray: "X-ray"};
    $("#revision-list").innerHTML = state.revisions.length ? state.revisions.map((item) => `<details class="revision-entry"><summary><strong>${escapeHtml(typeLabels[item.result_type] || item.result_type)} · revision ${item.revision_no}</strong><span>${escapeHtml(formatDate(item.created_at, true))} · ${escapeHtml(item.created_by_username || "이관 기록")}</span></summary><p class="muted small">변경 항목: ${escapeHtml((item.changed_fields || []).join(", ") || "기존자료 이관")} · SHA-256 ${escapeHtml(item.snapshot_sha256)}</p><pre>${escapeHtml(JSON.stringify(item.snapshot, null, 2))}</pre></details>`).join("") : '<p class="empty-copy">이 진료의 검사 변경 이력이 없습니다.</p>';
}

function renderTrends() {
    const hasPatient = Boolean(state.patient);
    $("#results-empty").classList.toggle("hidden", hasPatient);
    $("#results-content").classList.toggle("hidden", !hasPatient);
    $("#results-context").textContent = hasPatient ? `${state.patient.name} · 차트번호 ${state.patient.chart_number}` : "환자를 선택하면 시간에 따른 변화를 표시합니다.";
    if (!hasPatient) return;
    $("#trend-charts").innerHTML = [
        trendChartHtml("la_ao", "LA/Ao"), trendChartHtml("vhs", "VHS"),
        monitoringChartHtml("home_rr_rpm", "모니터링 Home RR"),
        monitoringChartHtml("systolic_bp_mmhg", "모니터링 BP"),
        `<article class="trend-chart"><h3>ACVIM Stage</h3><div class="stage-timeline">${state.trends.map((item) => `<span>${escapeHtml(formatDate(item.visit_at))}<strong>${escapeHtml(item.acvim_stage || "–")}</strong></span>`).join("") || '<p class="empty-copy">기록 없음</p>'}</div></article>`,
    ].join("");
    const current = state.trends.at(-1) || {};
    const previous = state.trends.at(-2) || {};
    $("#trend-table").innerHTML = `<table><thead><tr><th>항목</th><th>현재</th><th>이전</th><th>변화</th></tr></thead><tbody>${cardioMetricDefinitions.map(([key, label, unit]) => {
        const delta = current[key] !== null && current[key] !== undefined && previous[key] !== null && previous[key] !== undefined ? Number(current[key]) - Number(previous[key]) : null;
        return `<tr><td>${label}</td><td>${formatMetric(current[key], unit)}</td><td>${formatMetric(previous[key], unit)}</td><td class="${delta > 0 ? "trend-delta-up" : ""}">${delta === null ? "–" : `${delta > 0 ? "+" : ""}${formatMetric(delta)}`}</td></tr>`;
    }).join("")}<tr><td>Stage</td><td>${escapeHtml(current.acvim_stage || "–")}</td><td>${escapeHtml(previous.acvim_stage || "–")}</td><td>${current.acvim_stage && previous.acvim_stage && current.acvim_stage === previous.acvim_stage ? "유지" : "–"}</td></tr></tbody></table>`;
    renderMonitoring();
    renderRevisions();
}

$("#cardiac-exam-form").addEventListener("input", (event) => {
    if (["e_velocity_ms", "a_velocity_ms", "la_ao", "lviddn", "tr_vmax_ms"].includes(event.target.name)) updateEchoCalculation();
});
$("#cardiac-exam-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $('button[type="submit"]', event.currentTarget);
    button.disabled = true;
    try { await saveCardiacExam(); } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$("#save-ecg").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await saveEcg(); } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$("#save-lab").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await saveLab(); } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$("#monitoring-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.patient) return toast("먼저 환자를 선택하세요.", "error");
    const form = event.currentTarget;
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    const numberValue = (name) => form.elements[name].value === "" ? null : Number(form.elements[name].value);
    try {
        await api(`/api/patients/${state.patient.id}/monitoring`, {
            method: "POST",
            body: {
                encounter_id: state.encounter?.id || null,
                measured_at: form.elements.measured_at.value,
                source: form.elements.source.value,
                body_weight_kg: numberValue("body_weight_kg"),
                home_rr_rpm: numberValue("home_rr_rpm"),
                systolic_bp_mmhg: numberValue("systolic_bp_mmhg"),
                heart_rate_bpm: numberValue("heart_rate_bpm"),
                cough: form.elements.cough.checked,
                dyspnea: form.elements.dyspnea.checked,
                syncope: form.elements.syncope.checked,
                appetite: form.elements.appetite.value,
                medication_adherence: form.elements.medication_adherence.value,
                adverse_effects: form.elements.adverse_effects.value.trim() || null,
                notes: form.elements.notes.value.trim() || null,
            },
        });
        const data = await api(`/api/patients/${state.patient.id}/monitoring`);
        state.monitoring = data.items;
        form.reset();
        form.elements.measured_at.value = currentDatetimeLocal();
        renderTrends();
        toast("경과 모니터링 기록을 추가했습니다.");
    } catch (error) {
        handleError(error);
    } finally {
        button.disabled = Boolean(state.patient?.is_archived);
    }
});
$("#upload-echo-video").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await uploadEchoVideo(); } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$$('[data-diagnostic-tab]').forEach((button) => button.addEventListener("click", () => setDiagnosticTab(button.dataset.diagnosticTab)));
$$('[data-echo-subtab]').forEach((button) => button.addEventListener("click", () => activateEchoSubtab(button.dataset.echoSubtab)));
$$('[data-open-diagnostic]').forEach((button) => button.addEventListener("click", () => {
    navigateView("diagnostics");
    setDiagnosticTab(button.dataset.openDiagnostic);
}));
$("#save-visit-button").addEventListener("click", () => saveCardiacExam().catch(handleError));
$("#complete-visit-button").addEventListener("click", async (event) => {
    if (!state.commercial.workflow) return toast("진료를 선택하세요.", "error");
    if (state.commercial.workflow.stage !== "checkout") {
        const blocker = state.commercial.workflow.blockers[0]?.message;
        toast(blocker || `${workflowLabels[state.commercial.workflow.stage]} 단계를 먼저 완료하세요.`, "error");
        return;
    }
    const button = event.currentTarget;
    button.disabled = true;
    try {
        state.commercial.workflow = await api(`/api/encounters/${state.encounter.id}/workflow/transition`, {method: "POST", body: {}});
        state.encounter.workflow_stage = state.commercial.workflow.stage;
        reflectEncounterStatus();
        renderEncounters();
        renderWorkflow();
        await loadTodayDashboard();
        toast("임상·설명·처방·수납 조건을 확인하고 진료를 종료했습니다.");
    } catch (error) { handleError(error); await refreshCommercialState(); }
    finally { renderWorkflow(); }
});

function renderAppointmentPatientOptions() {
    const select = $('#appointment-form [name="patient_id"]');
    if (!select) return;
    const selected = select.value || state.patient?.id || "";
    select.innerHTML = '<option value="">환자 선택</option>' + state.patients.map((patient) =>
        `<option value="${patient.id}">${escapeHtml(patient.name)} · ${escapeHtml(patient.chart_number)}</option>`
    ).join("");
    if ([...select.options].some((option) => option.value === selected)) select.value = selected;
    const taskSelect = $('#task-form [name="patient_id"]');
    if (taskSelect) {
        const taskSelected = taskSelect.value || "";
        taskSelect.innerHTML = '<option value="">환자 없음</option>' + state.patients.map((patient) =>
            `<option value="${patient.id}">${escapeHtml(patient.name)} · ${escapeHtml(patient.chart_number)}</option>`
        ).join("");
        taskSelect.value = taskSelected;
    }
}

const appointmentStatusLabels = {
    scheduled: "예약", arrived: "내원", completed: "완료", cancelled: "취소", no_show: "미내원",
};

async function loadAppointments() {
    const dateInput = $("#schedule-date");
    if (!dateInput.value) dateInput.value = dateToISO(new Date());
    const data = await api(`/api/appointments?date=${dateInput.value}`);
    state.appointments = data.items;
    $("#schedule-count").textContent = `${data.items.length}건`;
    const root = $("#appointment-list");
    root.innerHTML = data.items.length ? data.items.map((item) => `
        <article class="appointment-row ${escapeHtml(item.status)}">
            <time>${escapeHtml(new Intl.DateTimeFormat("ko-KR", {hour: "2-digit", minute: "2-digit"}).format(new Date(item.starts_at)))}</time>
            <div><strong>${escapeHtml(item.patient_name)}</strong><span>${escapeHtml(item.chart_number)} · ${escapeHtml(item.purpose)}</span>${item.notes ? `<small>${escapeHtml(item.notes)}</small>` : ""}</div>
            <span class="status">${escapeHtml(appointmentStatusLabels[item.status] || item.status)}</span>
            <div class="appointment-actions">
                <button type="button" data-appointment-edit="${item.id}">수정</button>
                ${["scheduled", "arrived"].includes(item.status) && !item.encounter_id ? `<button type="button" class="primary" data-appointment-checkin="${item.id}" data-patient-id="${item.patient_id}">접수·진료 시작</button>` : ""}
                ${["scheduled", "arrived"].includes(item.status) ? `<button type="button" data-appointment-status="completed" data-appointment-id="${item.id}">완료</button><button type="button" data-appointment-status="cancelled" data-appointment-id="${item.id}">취소</button>` : ""}
            </div>
        </article>
    `).join("") : '<p class="empty-copy">이 날짜에 등록된 예약이 없습니다.</p>';
}

function resetAppointmentForm() {
    const form = $("#appointment-form");
    form.reset();
    form.elements.appointment_id.value = "";
    form.elements.duration_minutes.value = "30";
    $("#appointment-form-title").textContent = "예약 등록";
    $("#cancel-appointment-edit").classList.add("hidden");
    renderAppointmentPatientOptions();
}

$("#schedule-date").addEventListener("change", () => loadAppointments().catch(handleError));
$("#appointment-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    try {
        const payload = formObject(form);
        const appointmentId = payload.appointment_id;
        delete payload.appointment_id;
        payload.duration_minutes = Number(payload.duration_minutes);
        await api(appointmentId ? `/api/appointments/${appointmentId}` : "/api/appointments", {method: appointmentId ? "PATCH" : "POST", body: payload});
        const selectedDate = payload.starts_at.slice(0, 10);
        resetAppointmentForm();
        $("#schedule-date").value = selectedDate;
        await Promise.all([loadAppointments(), loadTodayDashboard()]);
        toast(appointmentId ? "예약을 수정했습니다." : "예약을 저장했습니다.");
    } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$("#appointment-list").addEventListener("click", async (event) => {
    const editButton = event.target.closest("[data-appointment-edit]");
    if (editButton) {
        const item = state.appointments.find((appointment) => appointment.id === editButton.dataset.appointmentEdit);
        if (!item) return;
        const form = $("#appointment-form");
        form.elements.appointment_id.value = item.id;
        form.elements.patient_id.value = item.patient_id;
        form.elements.starts_at.value = datetimeLocalValue(item.starts_at);
        form.elements.duration_minutes.value = item.duration_minutes;
        form.elements.purpose.value = item.purpose;
        form.elements.status.value = item.status;
        form.elements.notes.value = item.notes || "";
        $("#appointment-form-title").textContent = "예약 수정";
        $("#cancel-appointment-edit").classList.remove("hidden");
        form.scrollIntoView({behavior: "smooth", block: "start"});
        return;
    }
    const checkinButton = event.target.closest("[data-appointment-checkin]");
    if (checkinButton) {
        checkinButton.disabled = true;
        try {
            const data = await api(`/api/appointments/${checkinButton.dataset.appointmentCheckin}/check-in`, {method: "POST", body: {}});
            await Promise.all([loadAppointments(), loadTodayDashboard()]);
            navigateView("patients");
            await selectPatient(checkinButton.dataset.patientId);
            await openEncounter(data.encounter.id);
            toast(data.created ? "접수하고 진료를 시작했습니다." : "연결된 진료를 열었습니다.");
        } catch (error) { handleError(error); } finally { checkinButton.disabled = false; }
        return;
    }
    const button = event.target.closest("[data-appointment-status]");
    if (!button) return;
    button.disabled = true;
    try {
        await api(`/api/appointments/${button.dataset.appointmentId}`, {method: "PATCH", body: {status: button.dataset.appointmentStatus}});
        await Promise.all([loadAppointments(), loadTodayDashboard()]);
        toast("예약 상태를 변경했습니다.");
    } catch (error) { handleError(error); } finally { button.disabled = false; }
});
$("#cancel-appointment-edit").addEventListener("click", resetAppointmentForm);

function prescriptionItemHtml(item = {}) {
    return `<div class="prescription-item-row"><label>약품명<input data-medication-field="medication_name" value="${escapeHtml(item.medication_name || "")}"></label><label>1회 용량<input data-medication-field="dose_value" type="number" min="0.0001" step="0.0001" value="${item.dose_value ?? ""}"></label><label>단위<input data-medication-field="dose_unit" placeholder="mg, tablet" value="${escapeHtml(item.dose_unit || "")}"></label><label>경로<input data-medication-field="route" placeholder="PO" value="${escapeHtml(item.route || "")}"></label><label>빈도<input data-medication-field="frequency" placeholder="BID" value="${escapeHtml(item.frequency || "")}"></label><label>기간(일)<input data-medication-field="duration_days" type="number" min="1" max="3650" value="${item.duration_days ?? ""}"></label><label class="wide">주의사항<input data-medication-field="instructions" value="${escapeHtml(item.instructions || "")}"></label><button type="button" class="danger-ghost" data-remove-row>삭제</button></div>`;
}

function invoiceItemHtml(item = {}) {
    return `<div class="invoice-item-row"><label>항목<input data-invoice-field="description" value="${escapeHtml(item.description || "")}"></label><label>수량<input data-invoice-field="quantity" type="number" min="0.01" step="0.01" value="${item.quantity ?? 1}"></label><label>단가<input data-invoice-field="unit_amount" type="number" min="0" step="100" value="${item.unit_amount ?? 0}"></label><label>코드<input data-invoice-field="item_code" value="${escapeHtml(item.item_code || "")}"></label><button type="button" class="danger-ghost" data-remove-row>삭제</button></div>`;
}

function receiptHtml(paymentType, snapshot = {}) {
    const isRefund = paymentType === "refund";
    const items = Array.isArray(snapshot.items) ? snapshot.items : [];
    const amount = isRefund ? snapshot.refund : snapshot.amount;
    return `<div class="report-sheet receipt-sheet"><header><p class="eyebrow">${isRefund ? "REFUND RECEIPT" : "PAYMENT RECEIPT"}</p><h2>${isRefund ? "환불 확인서" : "영수증"}</h2><p>${escapeHtml(snapshot.receipt_number || "")} · ${escapeHtml(formatDate(snapshot.paid_at, true))}</p></header><section><p>청구서 ${escapeHtml(snapshot.invoice_number || "-")}</p>${snapshot.patient ? `<p>${escapeHtml(snapshot.patient.name || "")} · ${escapeHtml(snapshot.patient.chart_number || "")}</p>` : ""}</section>${items.length ? `<section><h3>청구 항목</h3>${items.map((item) => `<p>${escapeHtml(item.description)} · ${Number(item.quantity).toLocaleString("ko-KR")} × ${Number(item.unit_amount).toLocaleString("ko-KR")}원 = ${Number(item.line_amount).toLocaleString("ko-KR")}원</p>`).join("")}</section>` : ""}<section><h3>${isRefund ? "환불" : "결제"}</h3><p><strong>${Number(amount || 0).toLocaleString("ko-KR")}원</strong> · ${escapeHtml(snapshot.method || "")}</p>${snapshot.reason ? `<p>사유: ${escapeHtml(snapshot.reason)}</p>` : ""}</section><p class="review-warning">서버에 저장된 발행 당시 snapshot입니다.</p></div>`;
}

function reportHtml(type) {
    if (!state.patient || !state.encounter) return '<div class="page-empty"><h2>진료 선택 필요</h2><p>보고서를 만들 진료를 선택하세요.</p></div>';
    const exam = state.cardiacComparison?.exam || {};
    const sections = Object.fromEntries((state.soap?.sections || []).map((section) => [section.stage, section.current_text || "미확정"]));
    const patientLine = `${state.patient.name} · ${speciesLabel(state.patient.species)} · ${state.patient.breed || "품종 미입력"} · ${sexLabel(state.patient.sex, state.patient.neutered)}`;
    if (type === "prescription") {
        const item = state.commercial.prescription || {};
        const items = item.items?.length ? item.items : [{}];
        const stageAllowsEdit = ["education", "checkout"].includes(state.commercial.workflow?.stage);
        const canEdit = state.user?.role === "veterinarian" && stageAllowsEdit;
        if (!canEdit) {
            const status = {draft: "작성 중", issued: "발행", not_required: "처방 불필요", cancelled: "취소"}[item.status] || "미작성";
            const rows = item.items?.length
                ? item.items.map((row) => `<p>${escapeHtml(row.medication_name)} ${escapeHtml(row.dose_value)} ${escapeHtml(row.dose_unit)} · ${escapeHtml(row.route)} · ${escapeHtml(row.frequency)} · ${escapeHtml(row.duration_days)}일</p>`).join("")
                : "<p>처방 항목 없음</p>";
            const notice = state.commercial.workflow?.stage === "closed"
                ? "종료된 진료의 처방은 읽기 전용입니다."
                : (!stageAllowsEdit ? "SOAP 완료 후 보호자 설명 단계에서 처방을 작성합니다." : "처방 작성과 발행은 수의사가 담당합니다.");
            return `<div class="report-sheet"><header><p class="eyebrow">PRESCRIPTION</p><h2>구조화 처방전</h2><p>${escapeHtml(patientLine)}</p></header><section><h3>${escapeHtml(status)}</h3>${rows}<pre>${escapeHtml(item.instructions || item.not_required_reason || "")}</pre></section><p class="review-warning">${notice}</p></div>`;
        }
        return `<div class="report-sheet"><header><p class="eyebrow">PRESCRIPTION</p><h2>구조화 처방전</h2><p>${escapeHtml(patientLine)} · 체중 ${formatMetric(state.patient.weight_kg, "kg")} · 알레르기 ${escapeHtml(state.patient.allergies || "미확인")}</p></header><form id="prescription-form" class="report-entry-form"><label>상태<select name="status"><option value="draft" ${item.status === "draft" || !item.status ? "selected" : ""}>작성 중</option><option value="issued" ${item.status === "issued" ? "selected" : ""}>발행</option><option value="not_required" ${item.status === "not_required" ? "selected" : ""}>처방 불필요</option><option value="cancelled" ${item.status === "cancelled" ? "selected" : ""}>취소</option></select></label><label>불필요 사유<input name="not_required_reason" value="${escapeHtml(item.not_required_reason || "")}"></label><div id="prescription-items" class="prescription-items wide">${items.map(prescriptionItemHtml).join("")}</div><button type="button" data-add-prescription-item>약품 추가</button><label class="wide">전체 복약 안내<textarea name="instructions" rows="4">${escapeHtml(item.instructions || "")}</textarea></label><button type="submit" class="primary">처방 저장</button></form><p class="review-warning">발행 전 체중·현재 약물·알레르기를 확인하십시오.</p></div>`;
    }
    if (type === "billing") {
        if (!["checkout", "closed"].includes(state.commercial.workflow?.stage)) {
            return '<div class="page-empty"><h2>수납 단계 전입니다</h2><p>처방과 보호자 설명서 교부를 완료한 뒤 수납 단계로 진행하세요.</p></div>';
        }
        const invoice = state.commercial.invoice || {};
        const editable = !invoice.status || ["draft", "void"].includes(invoice.status);
        const items = invoice.items?.length ? invoice.items : [{description: "진찰료", quantity: 1, unit_amount: 0}, {description: "검사비", quantity: 1, unit_amount: 0}, {description: "약제비", quantity: 1, unit_amount: 0}];
        const balance = Math.max(0, Number(invoice.total_amount || 0) - Number(invoice.paid_amount || 0));
        const paymentHistory = (invoice.payments || []).map((payment) => `<p>${escapeHtml(payment.receipt_number)} · ${payment.payment_type === "refund" ? "환불" : "결제"} · ${Number(payment.amount).toLocaleString("ko-KR")}원 · ${escapeHtml(payment.payment_method)} <button type="button" class="link-button" data-receipt-id="${escapeHtml(payment.id)}">보기·인쇄</button></p>`).join("") || "<p>기록 없음</p>";
        const paymentForms = invoice.status && !["draft", "void"].includes(invoice.status) ? `<form id="payment-form" class="report-entry-form"><label>결제금액<input name="amount" type="number" min="1" max="${balance}" step="100" value="${balance}"></label><label>결제수단<select name="payment_method"><option value="card">카드</option><option value="cash">현금</option><option value="transfer">이체</option><option value="other">기타</option></select></label><label>결제일시<input name="paid_at" type="datetime-local" value="${datetimeLocalValue(new Date().toISOString())}"></label><button class="primary" ${balance <= 0 ? "disabled" : ""}>결제 기록</button></form>${Number(invoice.paid_amount || 0) > 0 ? `<form id="refund-form" class="report-entry-form"><label>환불금액<input name="amount" type="number" min="1" max="${invoice.paid_amount}" step="100" value="${invoice.paid_amount}"></label><label>환불수단<select name="payment_method"><option value="card">카드</option><option value="cash">현금</option><option value="transfer">이체</option><option value="other">기타</option></select></label><label>환불일시<input name="paid_at" type="datetime-local" value="${datetimeLocalValue(new Date().toISOString())}"></label><label class="wide">환불 사유<input name="reason" required maxlength="500"></label><button class="danger">환불 기록</button></form>` : ""}<form id="deferred-form" class="report-entry-form"><label class="wide">미수 사유<input name="reason" required maxlength="500" value="${escapeHtml(invoice.deferred_reason || "")}"></label><button class="secondary">미수 사유 기록</button></form><div class="commercial-actions"><button type="button" class="danger-ghost" data-void-invoice>청구서 취소·정정</button></div><section><h3>결제·환불 이력</h3>${paymentHistory}</section>` : "";
        const correctionNotice = invoice.status === "void" ? `<p class="review-warning">취소된 v${invoice.version_no} 청구서입니다. 아래 항목을 수정해 새 버전을 발행하세요. 사유: ${escapeHtml(invoice.void_reason || "-")}</p>` : "";
        return `<div class="report-sheet"><header><p class="eyebrow">INVOICE · PAYMENT</p><h2>청구·수납</h2><p>${escapeHtml(patientLine)} · ${escapeHtml(invoice.invoice_number || "미발행")}${invoice.version_no ? ` · v${invoice.version_no}` : ""}</p></header>${correctionNotice}<form id="invoice-form" class="report-entry-form"><div id="invoice-items" class="invoice-items wide">${items.map(invoiceItemHtml).join("")}</div><button type="button" data-add-invoice-item ${editable ? "" : "disabled"}>항목 추가</button><label>할인<input name="discount_amount" type="number" min="0" step="100" value="${invoice.discount_amount || 0}" ${editable ? "" : "disabled"}></label><label>미수 사유<input name="deferred_reason" maxlength="500" value="${escapeHtml(invoice.deferred_reason || "")}" ${editable ? "" : "disabled"}></label><div class="billing-total wide">총액 <strong>${Number(invoice.total_amount || 0).toLocaleString("ko-KR")}원</strong> · 결제 ${Number(invoice.paid_amount || 0).toLocaleString("ko-KR")}원 · 잔액 ${balance.toLocaleString("ko-KR")}원</div><div class="commercial-actions wide">${editable ? '<button type="submit" class="secondary">청구서 저장</button><button type="button" class="primary" data-issue-invoice>청구서 발행</button>' : ""}</div></form>${paymentForms}</div>`;
    }
    if (type === "echo") {
        return `<div class="report-sheet"><header><p class="eyebrow">ECHOCARDIOGRAPHY REPORT</p><h2>Echo Report</h2><p>${escapeHtml(patientLine)} · ${escapeHtml(formatDate(state.encounter.visit_at, true))}</p></header><section><h3>Measurements</h3><p>LA/Ao ${formatMetric(exam.la_ao)} · LVIDd ${formatMetric(exam.lvidd_cm, "cm")} · LVIDDN ${formatMetric(exam.lviddn)} · LVIDs ${formatMetric(exam.lvids_cm, "cm")} · FS ${formatMetric(exam.fs_percent, "%")}</p><p>E ${formatMetric(exam.e_velocity_ms, "m/s")} · A ${formatMetric(exam.a_velocity_ms, "m/s")} · E/A ${formatMetric(exam.e_a_ratio)} · TR Vmax ${formatMetric(exam.tr_vmax_ms, "m/s")}</p></section><section><h3>Findings</h3><pre>${escapeHtml(exam.findings || "미입력")}</pre></section><section><h3>Assessment</h3><pre>${escapeHtml(exam.assessment || "미입력")}</pre><p>ACVIM ${escapeHtml(exam.acvim_stage || "미확정")} · PH 위험 ${escapeHtml(exam.ph_risk || "미입력")} · CHF ${escapeHtml(exam.chf_status || "미입력")}</p></section><p class="review-warning">수의사 검토 후 사용하십시오.</p></div>`;
    }
    const education = state.commercial.education;
    let snapshot = null;
    try { snapshot = education ? JSON.parse(education.content_snapshot) : null; } catch (_error) { snapshot = null; }
    const canCreateEducation = state.user?.role === "veterinarian" && state.commercial.workflow?.stage === "education";
    const canDeliverEducation = education?.status === "draft" && state.commercial.workflow?.stage === "education";
    const deliveryStatus = education?.status === "delivered"
        ? `교부 완료 · ${formatDate(education.delivered_at, true)}`
        : (state.user?.role === "staff" && !education ? "수의사 문서 생성 필요" : "교부 전");
    return `<div class="report-sheet"><header><p class="eyebrow">CLIENT INFORMATION</p><h2>보호자 설명서</h2><p>${escapeHtml(patientLine)} · ${escapeHtml(formatDate(state.encounter.visit_at, true))}</p></header><section><h3>진단 및 평가</h3><pre>${escapeHtml(snapshot?.assessment || sections.A || "미확정")}</pre></section><section><h3>진료 계획</h3><pre>${escapeHtml(snapshot?.plan || sections.P || "미확정")}</pre></section><section><h3>응급 위험징후</h3><p>${escapeHtml(snapshot?.warning_signs || "호흡곤란, 실신, 청색증 또는 안정 시 호흡수 증가 시 즉시 병원에 문의하세요.")}</p></section><section><h3>다음 검사</h3><p>${exam.follow_up_months ? `${exam.follow_up_months}개월 후 재검 권장` : "담당 수의사와 재검 일정을 상의하세요."}</p></section><div class="commercial-actions">${canCreateEducation ? '<button type="button" class="secondary" data-create-education>확정 기록으로 문서 생성</button>' : ""}${canDeliverEducation ? '<button type="button" class="primary" data-deliver-education="print">인쇄 교부 확인</button><button type="button" data-deliver-education="pdf">PDF 교부 확인</button>' : ""}<span class="status ${education?.status === "delivered" ? "completed" : ""}">${escapeHtml(deliveryStatus)}</span></div><p class="review-warning">확정된 SOAP와 처방의 불변 snapshot으로 저장됩니다.</p></div>`;
}

$$('[data-report]').forEach((button) => button.addEventListener("click", () => {
    const type = button.dataset.report;
    const titles = {owner: "보호자 설명서", echo: "Echo Report", prescription: "처방", billing: "수납"};
    $("#report-title").textContent = titles[type];
    $("#report-content").innerHTML = reportHtml(type);
    navigateView("report");
}));
function collectRows(rootSelector, fieldSelector) {
    return $$(rootSelector).map((row) => Object.fromEntries($$(fieldSelector, row).map((input) => [input.dataset.medicationField || input.dataset.invoiceField, input.type === "number" ? Number(input.value) : input.value.trim()])));
}

async function saveInvoiceDraft() {
    const form = $("#invoice-form");
    const items = collectRows("#invoice-items .invoice-item-row", "[data-invoice-field]").filter((item) => item.description);
    const data = await api(`/api/encounters/${state.encounter.id}/invoice`, {method: "PUT", body: {items, discount_amount: Number(form.elements.discount_amount.value || 0), deferred_reason: form.elements.deferred_reason.value.trim() || null}});
    state.commercial.invoice = data.invoice;
    return data.invoice;
}

$("#report-content").addEventListener("click", async (event) => {
    const remove = event.target.closest("[data-remove-row]");
    if (remove) { remove.closest(".invoice-item-row, .prescription-item-row")?.remove(); return; }
    if (event.target.closest("[data-add-prescription-item]")) { $("#prescription-items").insertAdjacentHTML("beforeend", prescriptionItemHtml()); return; }
    if (event.target.closest("[data-add-invoice-item]")) { $("#invoice-items").insertAdjacentHTML("beforeend", invoiceItemHtml()); return; }
    const receipt = event.target.closest("[data-receipt-id]");
    if (receipt) {
        receipt.disabled = true;
        try {
            const data = await api(`/api/payments/${receipt.dataset.receiptId}/receipt`);
            $("#report-title").textContent = data.payment_type === "refund" ? "환불 확인서" : "영수증";
            $("#report-content").innerHTML = receiptHtml(data.payment_type, data.snapshot);
        } catch (error) { handleError(error); } finally { receipt.disabled = false; }
        return;
    }
    const voidInvoice = event.target.closest("[data-void-invoice]");
    if (voidInvoice) {
        const reason = window.prompt("청구서 취소·정정 사유를 입력하세요. 결제 잔액이 있으면 먼저 전액 환불해야 합니다.");
        if (!reason?.trim()) return;
        voidInvoice.disabled = true;
        try {
            const data = await api(`/api/encounters/${state.encounter.id}/invoice/void`, {method: "POST", body: {reason: reason.trim()}});
            state.commercial.invoice = data.invoice;
            $("#report-content").innerHTML = reportHtml("billing");
            await refreshCommercialState();
            toast("기존 청구서를 보존한 채 취소했습니다. 수정 후 새 버전을 발행하세요.");
        } catch (error) { handleError(error); } finally { voidInvoice.disabled = false; }
        return;
    }
    const issue = event.target.closest("[data-issue-invoice]");
    if (issue) {
        issue.disabled = true;
        try {
            await saveInvoiceDraft();
            const data = await api(`/api/encounters/${state.encounter.id}/invoice/issue`, {method: "POST", body: {}});
            state.commercial.invoice = data.invoice;
            $("#report-content").innerHTML = reportHtml("billing");
            await refreshCommercialState();
            toast("청구서를 발행했습니다.");
        } catch (error) { handleError(error); } finally { issue.disabled = false; }
        return;
    }
    const createEducation = event.target.closest("[data-create-education]");
    if (createEducation) {
        createEducation.disabled = true;
        try {
            const data = await api(`/api/encounters/${state.encounter.id}/education`, {method: "POST", body: {}});
            state.commercial.education = data.document;
            $("#report-content").innerHTML = reportHtml("owner");
            toast("보호자 설명서 snapshot을 생성했습니다.");
        } catch (error) { handleError(error); } finally { createEducation.disabled = false; }
        return;
    }
    const deliverEducation = event.target.closest("[data-deliver-education]");
    if (deliverEducation) {
        deliverEducation.disabled = true;
        try {
            const data = await api(`/api/encounters/${state.encounter.id}/education/deliver`, {method: "POST", body: {method: deliverEducation.dataset.deliverEducation}});
            state.commercial.education = data.document;
            $("#report-content").innerHTML = reportHtml("owner");
            await refreshCommercialState();
            navigateView("patients");
            $("#encounter-workspace").scrollIntoView({behavior: "smooth", block: "start"});
            toast("보호자 설명서 교부를 기록했습니다. 수납 단계로 진행하세요.");
        } catch (error) { handleError(error); } finally { deliverEducation.disabled = false; }
    }
});

$("#report-content").addEventListener("submit", async (event) => {
    if (!state.encounter) return;
    if (event.target.id === "prescription-form") {
        event.preventDefault();
        const form = event.target;
        const button = $('button[type="submit"]', form);
        button.disabled = true;
        try {
            const payload = formObject(form);
            payload.items = collectRows("#prescription-items .prescription-item-row", "[data-medication-field]").filter((item) => item.medication_name);
            const data = await api(`/api/encounters/${state.encounter.id}/structured-prescription`, {method: "PUT", body: payload});
            state.commercial.prescription = data.prescription;
            $("#report-content").innerHTML = reportHtml("prescription");
            await refreshCommercialState();
            if (["issued", "not_required"].includes(data.prescription.status)) {
                $("#report-title").textContent = "보호자 설명서";
                $("#report-content").innerHTML = reportHtml("owner");
                toast("처방을 확정했습니다. 보호자 설명서를 생성·교부하세요.");
            } else {
                toast("처방을 저장했습니다.");
            }
        } catch (error) { handleError(error); } finally { button.disabled = false; }
    } else if (event.target.id === "invoice-form") {
        event.preventDefault();
        const form = event.target;
        const button = $('button[type="submit"]', form);
        button.disabled = true;
        try {
            state.commercial.invoice = await saveInvoiceDraft();
            $("#report-content").innerHTML = reportHtml("billing");
            toast("청구서 초안을 저장했습니다.");
        } catch (error) { handleError(error); } finally { button.disabled = false; }
    } else if (event.target.id === "payment-form") {
        event.preventDefault();
        const form = event.target;
        const button = $('button[type="submit"]', form);
        button.disabled = true;
        try {
            const payload = formObject(form);
            payload.amount = Number(payload.amount);
            const data = await api(`/api/encounters/${state.encounter.id}/invoice/payments`, {method: "POST", body: payload});
            state.commercial.invoice = data.invoice;
            $("#report-content").innerHTML = reportHtml("billing");
            await Promise.all([loadTodayDashboard(), refreshCommercialState()]);
            if (Number(data.invoice.paid_amount) >= Number(data.invoice.total_amount)) {
                navigateView("patients");
                $("#encounter-workspace").scrollIntoView({behavior: "smooth", block: "start"});
                toast(`결제와 영수증 ${data.receipt_number}을 기록했습니다. 진료완료를 눌러 종료하세요.`);
            } else {
                toast(`결제와 영수증 ${data.receipt_number}을 기록했습니다.`);
            }
        } catch (error) { handleError(error); } finally { button.disabled = false; }
    } else if (event.target.id === "refund-form") {
        event.preventDefault();
        const form = event.target;
        const button = $('button[type="submit"]', form);
        button.disabled = true;
        try {
            const payload = formObject(form);
            payload.amount = Number(payload.amount);
            const data = await api(`/api/encounters/${state.encounter.id}/invoice/refunds`, {method: "POST", body: payload});
            state.commercial.invoice = data.invoice;
            $("#report-content").innerHTML = reportHtml("billing");
            await Promise.all([loadTodayDashboard(), refreshCommercialState()]);
            toast(`환불과 확인서 ${data.receipt_number}을 기록했습니다.`);
        } catch (error) { handleError(error); } finally { button.disabled = false; }
    } else if (event.target.id === "deferred-form") {
        event.preventDefault();
        const form = event.target;
        const button = $('button[type="submit"]', form);
        button.disabled = true;
        try {
            const data = await api(`/api/encounters/${state.encounter.id}/invoice/deferred-reason`, {method: "PATCH", body: {reason: form.elements.reason.value.trim()}});
            state.commercial.invoice = data.invoice;
            $("#report-content").innerHTML = reportHtml("billing");
            await refreshCommercialState();
            navigateView("patients");
            $("#encounter-workspace").scrollIntoView({behavior: "smooth", block: "start"});
            toast("미수 사유를 기록했습니다. 진료완료를 눌러 종료하세요.");
        } catch (error) { handleError(error); } finally { button.disabled = false; }
    }
});
$("#print-report-button").addEventListener("click", () => window.print());

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
}

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

function xrayPanelHtml() {
    const xrays = state.encounter?.xrays || [];
    const closed = state.commercial.workflow?.stage === "closed";
    const canRead = state.user?.role === "veterinarian" && !closed;
    const canUpload = !closed;
    const completed = xrays.filter((xray) => String(xray.reading_text || "").trim()).length;
    const cards = xrays.length ? xrays.map((xray) => `
        <article class="xray-card" data-xray="${xray.id}">
            <a class="xray-preview" href="${escapeHtml(xray.file_url)}" target="_blank" rel="noopener">
                <img src="${escapeHtml(xray.file_url)}" alt="${escapeHtml(xray.original_name)}">
            </a>
            <div class="xray-card-body">
                <div class="xray-card-heading">
                    <strong>${escapeHtml(xray.body_region || xray.original_name)}</strong>
                    <span class="status ${xray.reading_text ? "completed" : ""}">${xray.reading_text ? "판독 완료" : "판독 미입력"}</span>
                </div>
                <label>판독 소견
                    <textarea class="xray-reading" rows="4" placeholder="영상에서 확인한 객관적 소견을 입력하세요." ${canRead ? "" : "disabled"}>${escapeHtml(xray.reading_text || "")}</textarea>
                </label>
                <div class="form-actions">
                    ${canRead ? '<button type="button" class="secondary save-reading">소견 저장</button>' : ""}
                </div>
            </div>
        </article>
    `).join("") : `<p class="muted small xray-empty">등록된 X-ray가 없습니다.</p>`;
    return `
        <details class="objective-xrays">
            <summary>
                <span>
                    <strong>X-ray 및 판독 소견</strong>
                    <span class="muted small">객관적 영상검사 자료</span>
                </span>
                <span class="xray-summary">X-ray ${xrays.length}건 · 판독 완료 ${completed}건</span>
            </summary>
            <div class="objective-xray-body">
                <form class="xray-form xray-form-grid">
                    <label>X-ray 파일
                        <input name="file" type="file" accept="image/jpeg,image/png" required ${canUpload ? "" : "disabled"}>
                    </label>
                    <label>촬영 부위
                        <input name="body_region" placeholder="예: 흉부" ${canUpload ? "" : "disabled"}>
                    </label>
                    <label class="wide">판독 소견
                        <textarea name="reading_text" rows="3" placeholder="${canRead ? "수의사 판독 소견을 입력하세요." : "판독 소견은 수의사가 입력합니다."}" ${canRead ? "" : "disabled"}></textarea>
                    </label>
                    <div class="form-actions wide">
                        <span class="muted small">X-ray 원본은 AI에 전달되지 않고 판독 소견만 사용됩니다.</span>
                        <button type="submit" class="secondary" ${canUpload ? "" : "disabled"}>X-ray 추가</button>
                    </div>
                </form>
                <div class="xray-list">${cards}</div>
            </div>
        </details>
    `;
}

async function refreshAfterXray() {
    const [encounter, soap, revisions] = await Promise.all([
        api(`/api/encounters/${state.encounter.id}`),
        api(`/api/encounters/${state.encounter.id}/soap`),
        api(`/api/encounters/${state.encounter.id}/clinical-revisions`),
    ]);
    state.encounter = encounter;
    state.soap = soap;
    state.revisions = revisions.items;
    reflectEncounterStatus();
    renderEncounters();
    renderSoap();
    await refreshCommercialState();
    renderDiagnostics();
    setDiagnosticTab("xray");
}

function bindXrayControls(root) {
    const form = $(".xray-form", root);
    if (!form) return;
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!state.encounter) return;
        const button = $('button[type="submit"]', form);
        button.disabled = true;
        try {
            await api(`/api/encounters/${state.encounter.id}/xrays`, {
                method: "POST", body: new FormData(form),
            });
            await refreshAfterXray();
            toast("X-ray와 판독 소견을 저장했습니다.");
        } catch (error) {
            handleError(error);
            button.disabled = false;
        }
    });
    $$(".save-reading", root).forEach((button) => button.addEventListener("click", async () => {
        const card = button.closest("[data-xray]");
        button.disabled = true;
        try {
            await api(`/api/xrays/${card.dataset.xray}`, {
                method: "PATCH", body: {reading_text: $(".xray-reading", card).value},
            });
            await refreshAfterXray();
            toast("판독 소견을 저장했습니다.");
        } catch (error) {
            handleError(error);
            button.disabled = false;
        }
    }));
}

const stageNames = {
    S: "Subjective Evaluation",
    O: "Objective Evaluation",
    A: "Assessment",
    P: "Plan",
};
const stageStatusNames = {
    pending: "대기",
    generated: "초안",
    confirmed: "확정",
    stale: "재확인",
};

function evidenceHtml(items = []) {
    if (!items.length) return "";
    return `
        <details class="evidence">
            <summary>
                <span>근거 보기</span>
                <span class="evidence-count">${items.length}개</span>
            </summary>
            <div class="evidence-list">
                ${items.map((item) => {
        const pages = item.page_start ? ` p.${item.page_start}${item.page_end && item.page_end !== item.page_start ? `–${item.page_end}` : ""}` : "";
        const relatedText = item.excerpt_ko || item.excerpt || "";
        const sourceMeta = [item.source_edition, item.source_volume, item.source_heading].filter(Boolean).join(" · ");
        const sourceLabel = {
            internal_medicine_textbook: "Ettinger",
            critical_care_textbook: "Critical Care",
        }[item.source_type] || "레퍼런스";
        return `
                    <article class="evidence-item">
                        <div><span class="evidence-label">${sourceLabel}</span><div class="evidence-text">${escapeHtml(item.document_name)}${pages}${sourceMeta ? `<small>${escapeHtml(sourceMeta)}</small>` : ""}</div></div>
                        <div><span class="evidence-label">관련 내용</span><div class="evidence-text">${escapeHtml(item.excerpt_ko ? relatedText : relatedText.slice(0, 600))}</div></div>
                    </article>
                `;
    }).join("")}
            </div>
        </details>
    `;
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
    toast("AI 초안을 생성했습니다.");
}

function renderSoap() {
    const root = $("#soap-steps");
    const canClinicallyEdit = state.user?.role === "veterinarian" && state.commercial.workflow?.stage !== "closed";
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
        const notebookLabel = {
            A: "NotebookLM",
            P: "NotebookLM",
        }[section.stage];
        const notebookLink = notebookLabel && section.unlocked ? `
            <a
                class="secondary notebooklm-link"
                href="${NOTEBOOKLM_URL}"
                target="_blank"
                rel="noopener noreferrer"
                title="환자정보를 자동 전송하지 않고 NotebookLM을 새 탭에서 엽니다."
            >${notebookLabel} <span aria-hidden="true">↗</span></a>
        ` : "";
        return `
            <article class="soap-step ${expanded ? "expanded" : "collapsed"} ${section.unlocked ? "" : "locked"} ${section.stage === "O" ? "objective-step" : ""}" data-stage="${section.stage}">
                <button type="button" class="soap-step-header" aria-expanded="${expanded}">
                    <strong class="stage-label">${stageNames[section.stage]}</strong>
                    ${preview ? `<span class="soap-preview">${escapeHtml(preview)}</span>` : ""}
                    <span class="status ${section.status === "confirmed" ? "completed" : ""}">${escapeHtml(stageStatusNames[section.status] || section.status)}</span>
                    <span class="soap-chevron" aria-hidden="true"></span>
                </button>
                <div class="soap-step-body">
                    ${!section.unlocked ? `<p class="muted small">이전 단계를 확정하면 작성할 수 있습니다.</p>` : ""}
                    ${stale ? `<p class="stale-note">진료 정보가 변경되었습니다. 기록을 다시 확인해 주세요.</p>` : ""}
                    ${section.stage === "S" ? `
                        <div class="symptom-grid subjective-symptoms">
                            <label><input type="checkbox" data-symptom="cough" ${state.cardiacComparison?.exam?.cough ? "checked" : ""}> 기침</label>
                            <label><input type="checkbox" data-symptom="syncope" ${state.cardiacComparison?.exam?.syncope ? "checked" : ""}> 실신</label>
                            <label><input type="checkbox" data-symptom="exercise_intolerance" ${state.cardiacComparison?.exam?.exercise_intolerance ? "checked" : ""}> 운동불내성</label>
                            <label><input type="checkbox" data-symptom="nocturnal_tachypnea" ${state.cardiacComparison?.exam?.nocturnal_tachypnea ? "checked" : ""}> 야간호흡증가</label>
                        </div>
                    ` : ""}
                    ${section.stage === "O" ? `
                        <label>신체검사/기초 소견
                            <textarea class="objective-source" rows="4" placeholder="청진, 호흡수, 심박수, 활력징후 등">${escapeHtml(state.encounter?.physical_exam || "")}</textarea>
                        </label>
                        <div class="objective-vitals">
                            <span>HR <strong>${formatMetric(state.cardiacComparison?.exam?.heart_rate_bpm)}</strong></span>
                            <span>RR <strong>${formatMetric(state.cardiacComparison?.exam?.respiratory_rate_rpm)}</strong></span>
                            <span>BW <strong>${formatMetric(state.cardiacComparison?.exam?.body_weight_kg, "kg")}</strong></span>
                            <span>Murmur <strong>${escapeHtml(state.cardiacComparison?.exam?.murmur_grade || "–")}</strong></span>
                            <span>Rhythm <strong>${escapeHtml(state.cardiacComparison?.exam?.rhythm || "–")}</strong></span>
                        </div>
                    ` : ""}
                    <div class="soap-stage-actions">
                        <button class="secondary generate" ${section.unlocked && canClinicallyEdit ? "" : "disabled"}>${section.candidates.length ? "AI 초안 재생성" : "AI 초안 생성"}</button>
                        ${notebookLink}
                    </div>
                    ${section.candidates.length ? `
                        <details class="candidates-fold" ${candidatesOpen ? "open" : ""}>
                            <summary>AI 초안 ${section.candidates.length}개</summary>
                            <div class="candidates">${candidates}</div>
                        </details>
                    ` : ""}
                    ${section.stage === "A" ? `
                        <label>진단명
                            <input class="assessment-diagnosis" maxlength="255" placeholder="진단명을 입력하세요." value="${escapeHtml(section.diagnosis_name || "")}" ${section.unlocked && canClinicallyEdit ? "" : "disabled"}>
                        </label>
                    ` : ""}
                    <label>SOAP 기록<textarea class="soap-editor" rows="6" ${section.unlocked && canClinicallyEdit ? "" : "disabled"}>${escapeHtml(current)}</textarea></label>
                    <button class="primary confirm" ${section.unlocked && canClinicallyEdit ? "" : "disabled"}>기록 확정</button>
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
    $$('[data-symptom]', step).forEach((input) => input.addEventListener("change", () => {
        const target = $("#cardiac-exam-form").elements[input.dataset.symptom];
        if (target) target.checked = input.checked;
    }));
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
                    button.textContent = "AI 초안 생성";
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
        if (!content) return toast("SOAP 기록을 입력하세요.", "error");
        const diagnosisName = stage === "A" ? $(".assessment-diagnosis", step).value.trim() : null;
        if (stage === "A" && !diagnosisName) return toast("진단명을 입력하세요.", "error");
        button.disabled = true;
        try {
            state.soap = await api(`/api/encounters/${state.encounter.id}/soap/${stage}/confirm`, {
                method: "PUT",
                body: {
                    content,
                    candidate_id: state.selectedCandidates[stage] || null,
                    ...(stage === "A" ? {diagnosis_name: diagnosisName} : {}),
                },
            });
            state.encounter = await api(`/api/encounters/${state.encounter.id}`);
            reflectEncounterStatus();
            renderSoap();
            renderEncounters();
            await refreshCommercialState();
            toast(`${stageNames[stage]} 기록을 확정했습니다.`);
        } catch (error) { handleError(error); button.disabled = false; }
    });
}

async function loadAdmin() {
    if (!state.user) return;
    try {
        const [status, jobs] = await Promise.all([api("/api/admin/status"), api("/api/admin/index-jobs")]);
        $("#model-badge").textContent = `${status.ai.provider} · ${status.ai.model}`;
        const needsAttention = !status.qdrant.ok || status.security_warnings.length > 0;
        $("#admin-status-dot").classList.toggle("warning", needsAttention);
        $("#admin-tools-button").title = needsAttention
            ? "관리 도구 · 운영 설정 확인 필요"
            : "관리 도구 · 파일럿 운영 조건 정상";
        $("#knowledge-summary").innerHTML = `
            PDF ${status.knowledge_files.length}개 · Chunk ${status.counts.kb_chunks}개<br>
            Qdrant ${status.qdrant.ok ? "정상" : "연결 안 됨"}<br>
            ${status.security_warnings.map((item) => `<span class="error">${escapeHtml(item)}</span>`).join("<br>")}
        `;
        renderJobs(jobs.items);
    } catch (error) {
        if (error.status !== 401) console.warn("Admin status unavailable", error);
    }
}

async function loadUsers() {
    const section = $("#user-admin-section");
    if (state.user?.role !== "veterinarian") {
        section.classList.add("hidden");
        return;
    }
    section.classList.remove("hidden");
    const data = await api("/api/admin/users");
    $("#user-list").innerHTML = data.items.map((item) => `<span class="tag">${escapeHtml(item.username)} · ${item.role === "staff" ? "스태프" : "수의사"}${item.is_active ? "" : " · 비활성"}</span>`).join("");
}

async function loadAuditEvents() {
    let root = $("#audit-event-list");
    if (!root) {
        $("#admin-drawer").insertAdjacentHTML("beforeend", '<section class="admin-section"><div class="panel-heading"><div><p class="eyebrow">AUDIT</p><h2>최근 감사기록</h2></div></div><div id="audit-event-list" class="job-list"></div></section>');
        root = $("#audit-event-list");
    }
    const data = await api("/api/admin/audit-events?limit=50");
    root.innerHTML = data.items.map((item) => `<div class="job"><strong>${escapeHtml(item.action)}</strong><div>${escapeHtml(item.username || "system")} · ${escapeHtml(formatDate(item.created_at, true))}</div><small>${escapeHtml(item.entity_type || "-")} · ${escapeHtml(item.entity_id || "-")}</small></div>`).join("") || '<p class="muted small">감사기록이 없습니다.</p>';
}

$("#user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("button", form);
    button.disabled = true;
    try {
        await api("/api/admin/users", {method: "POST", body: formObject(form)});
        form.reset();
        await loadUsers();
        toast("사용자 계정을 추가했습니다.");
    } catch (error) { handleError(error); } finally { button.disabled = false; }
});

function openAdminDrawer() {
    $("#admin-drawer").classList.add("open");
    $("#admin-backdrop").classList.add("open");
    $("#admin-drawer").setAttribute("aria-hidden", "false");
    $("#admin-tools-button").setAttribute("aria-expanded", "true");
    document.body.classList.add("drawer-open");
    Promise.all([loadUsers(), loadAuditEvents()]).catch(handleError);
    $("#admin-drawer-close").focus();
}

function closeAdminDrawer() {
    const drawer = $("#admin-drawer");
    const backdrop = $("#admin-backdrop");
    if (!drawer || !backdrop) return;
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    $("#admin-tools-button")?.setAttribute("aria-expanded", "false");
    document.body.classList.remove("drawer-open");
}

$("#admin-tools-button").addEventListener("click", openAdminDrawer);
$("#admin-drawer-close").addEventListener("click", closeAdminDrawer);
$("#admin-backdrop").addEventListener("click", closeAdminDrawer);
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#admin-drawer").classList.contains("open")) {
        closeAdminDrawer();
        $("#admin-tools-button").focus();
    }
});

function renderJobs(jobs) {
    const phaseLabels = {
        hashing: "파일 확인",
        extracting: "PDF 텍스트 추출",
        embedding: "임베딩 생성",
        storing: "청크 저장",
        uploading: "Qdrant 저장",
        finalizing: "마무리",
    };
    const statusLabels = {
        queued: "대기 중",
        running: "진행 중",
        completed: "완료",
        failed: "실패",
    };
    $("#index-jobs").innerHTML = jobs.slice(0, 5).map((job) => {
        const done = job.processed_files + job.skipped_files + job.failed_files;
        const max = Math.max(job.total_files, 1);
        const phase = job.progress_phase || "pending";
        const progressCurrent = Math.max(0, Number(job.progress_current) || 0);
        const progressTotal = Math.max(0, Number(job.progress_total) || 0);
        const boundedCurrent = progressTotal
            ? Math.min(progressCurrent, progressTotal)
            : progressCurrent;
        const progressUnit = phase === "extracting" ? "페이지" : "청크";
        const progressPercent = progressTotal
            ? Math.floor((boundedCurrent / progressTotal) * 100)
            : null;
        const currentProgress = job.status === "running" ? `
            <div class="job-current">
                <div class="job-current-file">${job.current_source_key ? escapeHtml(job.current_source_key) : "작업 준비 중"}</div>
                <div>${escapeHtml(phaseLabels[phase] || "처리 중")}${progressTotal ? ` · ${boundedCurrent}/${progressTotal} ${progressUnit} (${progressPercent}%)` : ""}</div>
                ${progressTotal
                    ? `<progress value="${boundedCurrent}" max="${progressTotal}" aria-label="현재 파일 진행률"></progress>`
                    : '<progress aria-label="현재 파일 처리 중"></progress>'}
            </div>` : "";
        return `<div class="job"><strong>${job.job_type === "full" ? "전체" : "증분"} · ${escapeHtml(statusLabels[job.status] || job.status)}</strong>
            <div>${done}/${job.total_files} 파일${job.failed_files ? ` · 실패 ${job.failed_files}` : ""}</div>
            <progress value="${Math.min(done, max)}" max="${max}" aria-label="전체 파일 진행률"></progress>
            ${currentProgress}
            ${job.error_message ? `<div class="error">${escapeHtml(job.error_message)}</div>` : ""}
            ${job.item_error_message ? `<div class="error">${job.failed_source_key ? `${escapeHtml(job.failed_source_key)}: ` : ""}${escapeHtml(job.item_error_message)}</div>` : ""}
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
        patient_archived: "보관된 환자입니다. 환자를 복원한 뒤 새 진료를 작성하세요.",
        index_job_already_running: "이미 실행 중인 색인 작업이 있습니다.",
        ai_generation_failed: error.message,
        conflict: "이미 사용 중인 값이거나 참조 중인 데이터입니다.",
        permission_denied: "현재 계정에는 이 작업 권한이 없습니다.",
        diagnostics_not_reviewed: "선택한 검사를 모두 검토하거나 미실시 사유를 기록하세요.",
        physical_exam_required: "신체검사/기초 소견을 먼저 입력하세요.",
        business_day_closed: "이미 일마감된 날짜입니다.",
        encounter_closed: "종료된 진료입니다. 수의사가 진료를 다시 연 뒤 수정하세요.",
        diagnostic_result_required: "검사 결과를 먼저 기록한 뒤 검토 완료로 변경하세요.",
        daily_close_blocked: "미청구 진료를 먼저 처리한 뒤 일마감하세요.",
        invoice_outdated: "임상 기록 변경 후 청구서를 다시 확인·발행하세요.",
        workflow_stage_required: "현재 진료 단계를 먼저 완료한 뒤 진행하세요.",
    };
    const blockerMessage = error.data?.blockers?.map((item) => item.message).join(" · ");
    toast(blockerMessage || messages[error.data?.error] || error.message || "요청을 처리하지 못했습니다.", "error");
}

bootstrap();
