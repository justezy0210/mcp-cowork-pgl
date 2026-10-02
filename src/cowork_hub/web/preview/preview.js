/* Presentation-only fixtures. This preview never connects to Firebase or the hub. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const paths = {
    server: '<rect x="4" y="3" width="16" height="7" rx="2"/><rect x="4" y="14" width="16" height="7" rx="2"/><path d="M8 6.5h.01M8 17.5h.01M12 6.5h5M12 17.5h5"/>',
    leaf: '<path d="M20 4c-8-2-16 2-16 9a7 7 0 0 0 7 7c7 0 10-8 9-16Z"/><path d="m4 20 11-11M9 15v-5M9 15h5"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    archive: '<path d="M4 8h16v12H4ZM3 3h18v5H3ZM9 12h6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    key: '<circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-4-4 3-3m-6 0 3-3"/>',
    book: '<path d="M12 5v16M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z"/>',
  };
  const icon = (name) => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.info}</svg>`;
  document.querySelectorAll("[data-icon]").forEach((element) => { element.innerHTML = icon(element.dataset.icon); });

  // Preview permissions only. Live data must come from the authenticated /v1/cluster API.
  const currentUser = { id: "ezy", allowed_nodes: ["224", "226", "228", "229"] };
  const servers = [
    { id: "224", cpu: [384, 512], memory: [768, 1024], gpu: "4 / 4장 예약", model: "H100 NVL" },
    { id: "226", cpu: [8, 64], memory: [32, 256], gpu: "GPU 없음", model: "CPU 전용" },
    { id: "228", cpu: [24, 40], memory: [480, 512], gpu: "0 / 1장 예약", model: "GTX 1080 Ti" },
    { id: "229", cpu: [32, 64], memory: [256, 1024], gpu: "2 / 4장 예약", model: "RTX 3090" },
  ];
  const statuses = { running: "실행 중", waiting: "대기", done: "완료", failed: "실패", verified: "확인됨", review: "확인 필요" };
  const jobs = [
    { id: "JOB-0248", name: "벼 resequencing · read mapping", status: "running", server: "229", cpus: 16, memory: 64, gpu: 0, elapsed: "실행 1시간 24분", reason: "시작 알림 전송됨", submitted: "2026.09.26 13:07", started: "2026.09.26 13:08", ended: "—", environment: "ezy · mapping", description: "벼 계통별 reads를 기준 유전체에 정렬하는 예시 작업입니다." },
    { id: "JOB-0247", name: "단백질 구조 예측 · batch 03", status: "running", server: "224", cpus: 32, memory: 128, gpu: 1, elapsed: "실행 2시간 06분", reason: "GPU 1장 예약됨", submitted: "2026.09.26 12:22", started: "2026.09.26 12:26", ended: "—", environment: "ezy · prediction", description: "단백질 서열 배치의 구조를 예측하는 예시 작업입니다." },
    { id: "JOB-0249", name: "유전자 주석 · evidence merge", status: "waiting", server: "228", cpus: 8, memory: 64, gpu: 0, elapsed: "대기 18분", reason: "메모리 부족 · 64 GiB 신청 / 32 GiB 가능", submitted: "2026.09.26 14:14", started: "아직 시작되지 않음", ended: "—", environment: "ezy · annotation", description: "메모리 예약 가능량이 신청량보다 작아 대기 중입니다. 실행 가능한 조건이 되면 실행기가 자동으로 시작합니다." },
    { id: "JOB-0250", name: "표현형 모델 학습 · seedling", status: "waiting", server: "224", cpus: 16, memory: 64, gpu: 1, elapsed: "대기 7분", reason: "GPU 대기 · 1장 신청 / 0장 가능", submitted: "2026.09.26 14:25", started: "아직 시작되지 않음", ended: "—", environment: "ezy · training", description: "현재 모든 GPU가 예약되어 있습니다. 종료 시각을 알 수 없어 예상 시작 시각은 표시하지 않습니다." },
    { id: "JOB-0246", name: "FASTQ 품질 확인 · rice panel", status: "done", server: "226", cpus: 8, memory: 16, gpu: 0, elapsed: "소요 32분", reason: "종료 알림 전송됨", submitted: "2026.09.26 11:01", started: "2026.09.26 11:01", ended: "2026.09.26 11:33", environment: "ezy · quality-check", description: "샘플별 raw reads의 품질 지표를 확인한 예시 작업입니다." },
    { id: "JOB-0245", name: "발현량 정량 · pilot run", status: "failed", server: "229", cpus: 8, memory: 32, gpu: 0, elapsed: "실행 4분 후 종료", reason: "입력 파일 확인 필요", submitted: "2026.09.26 10:12", started: "2026.09.26 10:12", ended: "2026.09.26 10:16", environment: "ezy · expression", description: "입력 파일 경로를 찾지 못해 종료된 예시입니다. 경로를 확인한 뒤 MCP 또는 CLI에서 다시 제출합니다." },
  ];
  const samples = [
    { id: "SEED-0241", species: "벼", tissue: "종자", name: "벼 · Nipponbare", type: "종자", line: "Nipponbare", generation: "원종 유지", quantity: "120", unit: "립", location: "seed", position: "A1", owner: "ezy", status: "verified", date: "2026.09.24", data: "DATA-0081" },
    { id: "SEED-0242", species: "벼", tissue: "종자", name: "벼 · IR64", type: "종자", line: "IR64", generation: "원종 유지", quantity: "85", unit: "립", location: "seed", position: "A2", owner: "ezy", status: "verified", date: "2026.09.24", data: "DATA-0082" },
    { id: "SEED-0243", species: "벼", tissue: "종자", name: "벼 · PGL-017", type: "종자", line: "PGL-017", generation: "F3", quantity: "42", unit: "립", location: "seed", position: "A3", owner: "ezy", status: "verified", date: "2026.09.23", data: null },
    { id: "SEED-0244", species: "벼", tissue: "종자", name: "벼 · PGL-021", type: "종자", line: "PGL-021", generation: "F4", quantity: "18", unit: "립", location: "seed", position: "B1", owner: "ezy", status: "review", date: "2026.09.18", data: null },
    { id: "TISSUE-0108", species: "벼", tissue: "잎", name: "Nipponbare · 잎 조직", type: "조직", line: "Nipponbare", generation: "—", quantity: "3", unit: "튜브", location: "tissue", position: "A1", owner: "ezy", status: "verified", date: "2026.09.25", data: "DATA-0083" },
    { id: "TISSUE-0109", species: "벼", tissue: "뿌리", name: "IR64 · 뿌리 조직", type: "조직", line: "IR64", generation: "—", quantity: "2", unit: "튜브", location: "tissue", position: "A2", owner: "ezy", status: "verified", date: "2026.09.25", data: null },
    { id: "DNA-0063", species: "벼", tissue: "잎", name: "Nipponbare · genomic DNA", type: "DNA", line: "Nipponbare", generation: "—", quantity: "48", unit: "µL", location: "dna", position: "C3", owner: "ezy", status: "verified", date: "2026.09.25", data: "DATA-0081" },
    { id: "DNA-0064", species: "벼", tissue: "뿌리", name: "IR64 · genomic DNA", type: "DNA", line: "IR64", generation: "—", quantity: "36", unit: "µL", location: "dna", position: "C4", owner: "ezy", status: "verified", date: "2026.09.25", data: "DATA-0082" },
  ];
  const locations = {
    seed: { name: "보관장 A", path: "종자 보관실 / 보관장 A / 선반 02 / 박스 S-04", box: "S-04" },
    tissue: { name: "초저온 냉동고 01", path: "시료 보관실 / 초저온 냉동고 01 / 랙 B / 박스 T-02", box: "T-02" },
    dna: { name: "냉동고 02", path: "시료 보관실 / 냉동고 02 / 선반 01 / 박스 D-01", box: "D-01" },
  };
  const datasets = [
    { id: "DATA-0081", name: "Nipponbare · WGS", format: "FASTQ", size: "42.6 GiB", files: 2, status: "verified", sample: "DNA-0063", path: "/10Gdata/demo/rice-panel/raw/Nipponbare/", checked: "2026.09.26 14:10", description: "벼 기준 계통의 paired-end whole-genome sequencing 데이터", events: [
      { title: "보관 경로 변경", date: "2026.09.26 14:10", source: "전용 CLI · ezy", before: "/10Gdata/demo/downloads/run_042/", after: "/10Gdata/demo/rice-panel/raw/Nipponbare/", note: "이동 결과 확인 후 위치 기록을 갱신했습니다." },
      { title: "파일 이름 변경", date: "2026.09.26 13:52", source: "에이전트의 전용 도구 · ezy", before: "sample_001_R1.fastq.gz", after: "Nipponbare_R1.fastq.gz", note: "데이터 ID는 유지됩니다." },
      { title: "다운로드 완료 · 최초 등록", date: "2026.09.25 18:24", source: "전용 CLI · ezy", note: "FASTQ 파일 2개의 다운로드 완료를 기록했습니다." },
    ] },
    { id: "DATA-0082", name: "IR64 · WGS", format: "FASTQ", size: "38.2 GiB", files: 2, status: "verified", sample: "DNA-0064", path: "/10Gdata/demo/rice-panel/raw/IR64/", checked: "2026.09.25 19:42", description: "IR64 계통의 paired-end whole-genome sequencing 데이터", events: [
      { title: "보관 경로 변경", date: "2026.09.25 19:42", source: "전용 CLI · ezy", before: "/10Gdata/demo/downloads/run_043/", after: "/10Gdata/demo/rice-panel/raw/IR64/", note: "이동 결과 확인 후 위치 기록을 갱신했습니다." },
      { title: "다운로드 완료 · 최초 등록", date: "2026.09.25 17:03", source: "전용 CLI · ezy", note: "FASTQ 파일 2개를 등록했습니다." },
    ] },
    { id: "DATA-0083", name: "Nipponbare · RNA-seq", format: "FASTQ", size: "12.8 GiB", files: 2, status: "review", sample: "TISSUE-0108", path: "/10Gdata/demo/rna-seq/incoming/Nipponbare/", checked: "2026.09.24 16:05", description: "Nipponbare 잎 조직의 RNA sequencing 데이터", events: [
      { title: "경로 변경 결과 확인 필요", date: "2026.09.26 09:12", source: "전용 CLI · ezy", note: "이동 작업 중 연결이 끊겼습니다. 현재 경로를 확정할 수 없어 마지막 확인 위치를 유지합니다." },
      { title: "다운로드 완료 · 최초 등록", date: "2026.09.24 16:05", source: "전용 CLI · ezy", note: "FASTQ 파일 2개를 등록했습니다." },
    ] },
  ];
  const state = { status: "all", server: "all", jobSearch: "", species: "all", type: "all", sampleSearch: "", dataSearch: "", dataset: "DATA-0081" };
  const badge = (status) => `<span class="badge ${escape(status)}">${escape(statuses[status])}</span>`;
  const field = (name, value) => `<div class="detail-field"><dt>${escape(name)}</dt><dd>${escape(value)}</dd></div>`;
  const matches = (query, ...values) => values.join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());

  const pagination = {
    job: { page: 1, size: 5, label: "작업", render: renderJobs },
    sample: { page: 1, size: 5, label: "샘플", render: renderSamples },
    data: { page: 1, size: 5, label: "데이터셋", render: renderData },
  };

  function paginate(items, list, total) {
    const paging = pagination[list];
    const pages = Math.ceil(items.length / paging.size);
    paging.page = Math.max(1, Math.min(paging.page, pages));
    const start = (paging.page - 1) * paging.size;
    const end = Math.min(start + paging.size, items.length);
    const controls = $(`${list}-pagination`);
    if (!controls.childElementCount) {
      controls.innerHTML = `<label class="page-size">페이지당 <select data-page-size="${list}" aria-label="${paging.label} 페이지당 항목 수">${[5, 10, 25].map((size) => `<option value="${size}">${size}개</option>`).join("")}</select></label><div class="page-buttons"><button type="button" data-list="${list}" data-page-step="-1" aria-label="${paging.label} 이전 페이지">이전</button><span class="page-position" aria-live="polite" aria-atomic="true"></span><button type="button" data-list="${list}" data-page-step="1" aria-label="${paging.label} 다음 페이지">다음</button></div>`;
    }
    controls.querySelector("select").value = String(paging.size);
    controls.querySelector(".page-position").textContent = pages ? `${paging.page} / ${pages}` : "페이지 없음";
    controls.querySelector('[data-page-step="-1"]').disabled = paging.page === 1;
    controls.querySelector('[data-page-step="1"]').disabled = paging.page >= pages;
    $(`${list}-result`).textContent = (items.length ? `${start + 1}–${end} / ${items.length}건` : "검색 결과 0건") + (items.length !== total ? ` · 전체 ${total}건` : "");
    return items.slice(start, end);
  }

  function renderServers() {
    const visibleServers = servers.filter((server) => currentUser.allowed_nodes.includes(server.id));
    $("server-count").textContent = visibleServers.length;
    if (state.server !== "all" && !visibleServers.some((server) => server.id === state.server)) {
      state.server = "all";
      pagination.job.page = 1;
    }
    $("server-filter").innerHTML = '<option value="all">전체 작업</option>' + visibleServers.map((server) => `<option value="${escape(server.id)}">${escape(server.id)}</option>`).join("");
    $("server-filter").value = state.server;
    $("server-filter").disabled = visibleServers.length === 0;
    const resource = (server, label, values, unit) => `<div class="resource"><div class="resource-label"><span>${label} 예약</span><strong>${values[0]} <span>/ ${values[1]}${unit}</span></strong></div><meter min="0" max="${values[1]}" value="${values[0]}" aria-label="${server} 서버 ${label} 예약 ${values[0]} / ${values[1]}${unit}">${values[0]} / ${values[1]}</meter></div>`;
    $("server-grid").innerHTML = visibleServers.map((server) => `<article class="server-card" aria-label="${server.id} 서버 예약 현황"><div class="server-card-top"><span class="server-id">${icon("server")}${server.id}</span><span class="status-dot">연결됨 · 예시</span></div>${resource(server.id, "CPU", server.cpu, "")}${resource(server.id, "메모리", server.memory, " GiB")}<div class="gpu-line"><span>${server.model}</span><strong>${server.gpu}</strong></div></article>`).join("") || '<div class="server-empty" role="status"><strong>허용된 서버가 없습니다.</strong><p>관리자에게 서버 사용 권한을 요청하세요.</p></div>';
  }

  function renderJobs() {
    const items = jobs.filter((job) => (state.status === "all" || job.status === state.status) && (state.server === "all" || job.server === state.server) && matches(state.jobSearch, job.name, job.id));
    $("job-rows").innerHTML = paginate(items, "job", jobs.length).map((job) => `<tr><td><button type="button" class="row-name" data-job="${job.id}">${escape(job.name)}</button><span class="row-sub">${job.id} · ezy</span><span class="mobile-row-meta">서버 ${job.server} · ${job.cpus} CPU · ${job.memory} GiB${job.gpu ? ` · GPU ${job.gpu}장` : ""}</span>${job.status === "waiting" || job.status === "failed" ? `<span class="mobile-row-meta warning-text">${escape(job.reason)}</span>` : ""}</td><td>${badge(job.status)}</td><td><span class="server-chip">${job.server}</span></td><td class="resource-cell">${job.cpus} CPU · ${job.memory} GiB${job.gpu ? `<span class="row-sub">GPU ${job.gpu}장</span>` : ""}</td><td class="resource-cell">${job.elapsed}</td><td class="reason-cell ${job.status}">${escape(job.reason)}</td></tr>`).join("") || '<tr><td colspan="6" class="empty-cell">조건에 맞는 작업이 없습니다. 검색어나 필터를 변경해 보세요.</td></tr>';
    $("job-filters").querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.status === state.status)));
  }

  function renderSpecies() {
    const species = [...new Set(samples.map((sample) => sample.species))].sort((a, b) => a.localeCompare(b, "ko"));
    $("species-filters").innerHTML = `<button type="button" data-species="all" aria-pressed="true"><span>전체 종</span><span>${samples.length}</span></button>` + species.map((name) => `<button type="button" data-species="${escape(name)}" aria-pressed="false"><span>${escape(name)}</span><span>${samples.filter((sample) => sample.species === name).length}</span></button>`).join("");
  }

  function renderSamples() {
    const items = samples.filter((sample) => (state.species === "all" || sample.species === state.species) && (state.type === "all" || sample.type === state.type) && matches(state.sampleSearch, sample.name, sample.id, sample.line, sample.species, sample.tissue || "미기록"));
    $("sample-rows").innerHTML = paginate(items, "sample", samples.length).map((sample) => `<tr><td><button type="button" class="row-name" data-sample="${sample.id}">${escape(sample.name)}</button><span class="row-sub">${sample.id}</span><span class="mobile-row-meta">${escape(sample.type)} · 조직·부위: ${escape(sample.tissue || "미기록")}</span><span class="mobile-row-meta">${sample.quantity} ${sample.unit} · ${locations[sample.location].box} / ${sample.position}</span></td><td><span class="type-label">${sample.type}</span></td><td>${escape(sample.tissue || "미기록")}</td><td>${sample.quantity} <span class="muted">${sample.unit}</span></td><td><span class="server-chip">${locations[sample.location].box} / ${sample.position}</span></td><td>${badge(sample.status)}</td></tr>`).join("") || '<tr><td colspan="6" class="empty-cell">조건에 맞는 샘플이 없습니다. 검색어나 종·유형 필터를 변경해 보세요.</td></tr>';
    $("sample-list-title").textContent = state.species === "all" ? "전체 컬렉션" : `${state.species} 컬렉션`;
    $("species-description").textContent = state.species === "all" ? "종자와 파생 시료를 함께 조회합니다." : `${state.species}의 종자와 파생 시료를 함께 조회합니다.`;
    $("species-filters").querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.species === state.species)));
  }

  function renderData() {
    const items = datasets.filter((item) => matches(state.dataSearch, item.name, item.id));
    const visibleItems = paginate(items, "data", datasets.length);
    if (!visibleItems.some((item) => item.id === state.dataset)) state.dataset = visibleItems[0]?.id || null;
    $("dataset-list").innerHTML = visibleItems.map((item) => `<button type="button" class="dataset-item" data-dataset="${item.id}" aria-pressed="${item.id === state.dataset}"><strong>${escape(item.name)}</strong><small>${item.id} · ${item.size}</small>${badge(item.status)}</button>`).join("");
    const item = datasets.find((dataset) => dataset.id === state.dataset);
    if (!item) { $("dataset-detail").innerHTML = '<div class="empty-cell">검색어를 변경하면 데이터셋의 위치와 이력을 확인할 수 있습니다.</div>'; return; }
    const sample = samples.find((sample) => sample.id === item.sample);
    $("dataset-detail").innerHTML = `<header class="dataset-heading"><div class="dataset-heading-top"><span class="eyebrow">${item.id}</span>${badge(item.status)}</div><h2>${escape(item.name)}</h2><p>${escape(item.description)}</p><div class="dataset-meta"><span>${item.format} · ${item.files}개 파일</span><span>${item.size}</span><span>담당자 ezy</span></div></header><div class="dataset-body">${item.status === "review" ? '<div class="review-note">이동 결과가 아직 확인되지 않았습니다. 아래 경로는 마지막으로 확인된 위치입니다.</div>' : ""}<h3 class="section-label">기록된 위치</h3><div class="path-block"><div class="path-top"><span>10G NFS · 연구 데이터</span><button type="button" class="button" data-copy-path="${item.id}">${icon("copy")}경로 복사</button></div><code>${escape(item.path)}</code></div><p class="path-note">마지막 확인 ${item.checked} · 전용 도구에서 확인된 기록입니다.</p><section class="data-section"><h3 class="section-label">연결된 샘플</h3><button type="button" class="relation-link" data-sample="${sample.id}">${icon("leaf")}${sample.id} · ${escape(sample.name)}${icon("arrow")}</button></section><section class="data-section"><h3 class="section-label">변경 이력 <span class="count">${item.events.length}</span></h3><ol class="timeline">${item.events.map((event) => `<li><div class="timeline-head"><strong>${escape(event.title)}</strong><time>${event.date}</time></div><p>${escape(event.source)}</p>${event.before ? `<div class="history-paths"><code class="previous-path">${escape(event.before)}</code><span class="path-arrow" aria-label="변경 후">↓</span><code>${escape(event.after)}</code></div>` : ""}<p>${escape(event.note)}</p></li>`).join("")}</ol></section></div>`;
  }

  function openDetail(kind, content) {
    $("detail-kind").textContent = kind;
    $("detail-content").innerHTML = content;
    if (!$("detail-dialog").open) $("detail-dialog").showModal();
    $("detail-dialog").scrollTop = 0;
  }

  function showJob(id) {
    const job = jobs.find((job) => job.id === id);
    if (!job) return;
    openDetail("JOB DETAILS · 예시", `<h2 id="detail-title">${escape(job.name)}</h2><p class="detail-id">${job.id} · ezy</p>${badge(job.status)}<section class="detail-section"><h3>${job.status === "waiting" ? "대기 이유" : "작업 상태"}</h3><p>${escape(job.description)}</p></section><section class="detail-section"><h3>실행 환경 · 신청 자원</h3><dl>${field("서버", job.server)}${field("환경", job.environment)}${field("CPU", `${job.cpus}개`)}${field("메모리", `${job.memory} GiB`)}${field("GPU", job.gpu ? `${job.gpu}장 · 단독 예약` : "신청하지 않음")}</dl></section><section class="detail-section"><h3>작업 시간</h3><dl>${field("접수", job.submitted)}${field("시작", job.started)}${field("종료", job.ended)}</dl></section><section class="detail-section"><h3>알림</h3><p>${job.status === "waiting" ? "실제 시작·종료 시 실행기가 자동으로 상태를 보고하고 Discord 알림을 보냅니다. 에이전트가 기다릴 필요가 없습니다." : escape(job.reason)}</p><p class="muted">이 시안은 알림을 전송하지 않습니다.</p></section>`);
  }

  function showSample(id) {
    const sample = samples.find((sample) => sample.id === id);
    if (!sample) return;
    const location = locations[sample.location];
    const cells = ["A", "B", "C"].flatMap((row) => Array.from({ length: 6 }, (_, column) => row + (column + 1)));
    openDetail("SAMPLE DETAILS · 예시", `<h2 id="detail-title">${escape(sample.name)}</h2><p class="detail-id">${sample.id} · ${sample.type}</p>${badge(sample.status)}${sample.status === "review" ? '<section class="detail-section"><div class="review-note">실물 수량을 다시 확인해야 합니다. 아래 수량은 마지막 기록값입니다.</div></section>' : ""}<section class="detail-section"><h3>기본 정보</h3><dl>${field("종", sample.species)}${field("유형", sample.type)}${field("조직·부위", sample.tissue || "미기록")}${field("계통", sample.line)}${field("세대", sample.generation)}${field("기록된 수량", `${sample.quantity} ${sample.unit}`)}${field("담당자", sample.owner)}${field("마지막 확인", sample.date)}</dl></section><section class="detail-section"><h3>보관 위치</h3><p>${location.path}</p><div class="box-preview" role="img" aria-label="예시 박스 ${location.box}, 선택 샘플의 위치 ${sample.position}">${cells.map((cell) => `<span class="box-cell ${cell === sample.position ? "current" : ""}">${cell}</span>`).join("")}</div><div class="box-caption">박스 ${location.box} · 선택 위치 ${sample.position} 강조</div></section><section class="detail-section"><h3>관련 데이터</h3>${sample.data ? `<p><button type="button" class="relation-link" data-related-dataset="${sample.data}">${icon("folder")}${sample.data} 데이터 보기${icon("arrow")}</button></p>` : '<p>아직 연결된 데이터가 없습니다.</p>'}</section>`);
  }

  const pageNames = { jobs: "서버·작업", samples: "종자·샘플", data: "데이터", access: "서버 사용 신청", tokens: "개인 토큰", guide: "MCP 설치·사용" };
  const scrollPositions = {};
  let currentPage;
  function showPage() {
    const requested = location.hash.slice(1);
    const page = Object.hasOwn(pageNames, requested) ? requested : "jobs";
    if (currentPage) scrollPositions[currentPage] = window.scrollY;
    document.querySelectorAll(".page").forEach((section) => { section.hidden = section.id !== `page-${page}`; });
    document.querySelectorAll("[data-page]").forEach((link) => {
      if (link.dataset.page === page) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    $("breadcrumb-current").textContent = pageNames[page];
    document.title = `${pageNames[page]} · Cowork PGL 시안`;
    if (currentPage) { $("main").focus({ preventScroll: true }); window.scrollTo(0, scrollPositions[page] || 0); }
    currentPage = page;
  }

  let toastTimer;
  function notify(message) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
  }

  function renderAccess() {
    $("access-grants").innerHTML = currentUser.allowed_nodes.map((id) => `<span class="server-chip">${escape(id)}</span>`).join("") || '<p class="muted">아직 허용된 서버가 없습니다.</p>';
    $("access-preview").disabled = false;
  }

  function renderTokenLink() {
    // Only the hub's known preview route has the real token app alongside it.
    // A downloaded HTML file must not invent a live token endpoint or credential.
    if (!["https:", "http:"].includes(location.protocol) || !/\/web\/preview\/(?:index\.html)?$/.test(location.pathname)) return;
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
    if (location.protocol !== "https:" && !local) {
      $("token-portal-note").textContent = "개인 토큰 발급에는 HTTPS 접속이 필요합니다. 관리자가 안내한 HTTPS 토큰 페이지를 사용하세요.";
      return;
    }
    $("token-portal-link").href = new URL("../", location.href).href;
    $("token-portal-link").removeAttribute("aria-disabled");
    $("token-portal-note").textContent = "기존 토큰 발급 화면으로 이동합니다. 웹 로그인과 본인 허브 계정 연결이 준비되어 있어야 발급할 수 있습니다.";
  }

  function clearAccessReview() {
    $("access-review").hidden = true;
    $("access-summary").textContent = "";
    $("access-error").hidden = true;
    $("access-form").querySelectorAll("input").forEach((input) => input.removeAttribute("aria-invalid"));
  }

  function accessError(id, message) {
    $("access-error").textContent = message;
    $("access-error").hidden = false;
    $(id).setAttribute("aria-invalid", "true");
    $(id).focus();
  }

  $("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    clearAccessReview();
    const node = $("access-node").value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(node)) {
      accessError("access-node", "서버 ID 한 개를 입력하세요.");
      return;
    }
    if (currentUser.allowed_nodes.includes(node)) {
      accessError("access-node", `${node} 서버는 이미 허용되어 있습니다.`);
      return;
    }
    const values = {};
    for (const [key, label, min, max] of [["uid", "UID", 0, 4294967295], ["gid", "GID", 0, 4294967295], ["port", "SSH 포트", 1, 65535]]) {
      const raw = $(`access-${key}`).value.trim();
      const value = Number(raw);
      if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) {
        accessError(`access-${key}`, `${label}: ${min}~${max} 사이의 정수를 입력하세요.`);
        return;
      }
      values[key] = value;
    }
    $("access-summary").textContent = `서버 사용 신청 · 미전송\n신청자: ${currentUser.id}\n신청 서버: ${node}\nUID: ${values.uid}\nGID: ${values.gid}\n컨테이너 SSH 포트: ${values.port}`;
    $("access-review").hidden = false;
    $("access-review").focus();
    $("access-review").scrollIntoView({ block: "nearest" });
  });
  $("access-form").addEventListener("input", clearAccessReview);

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const data = button.dataset;
    if (data.guideTab) {
      document.querySelectorAll("[data-guide-panel]").forEach((panel) => { panel.hidden = panel.dataset.guidePanel !== data.guideTab; });
      $("guide-tabs").querySelectorAll("button").forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.guideTab === data.guideTab)));
      $("guide-tabs").querySelector('[aria-pressed="true"]').focus({ preventScroll: true });
      $("guide-tabs").scrollIntoView({ block: "nearest" });
    }
    if (data.guideClient) {
      document.querySelectorAll("[data-client-panel]").forEach((panel) => { panel.hidden = panel.dataset.clientPanel !== data.guideClient; });
      document.querySelectorAll("[data-guide-client]").forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.guideClient === data.guideClient)));
    }
    if (data.copyCode) {
      const content = $(data.copyCode);
      try {
        await navigator.clipboard.writeText(content.textContent);
        notify("내용을 복사했습니다.");
      } catch {
        const range = document.createRange();
        range.selectNodeContents(content);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        content.focus({ preventScroll: true });
        notify("내용을 선택했습니다. Ctrl+C 또는 길게 눌러 복사하세요.");
      }
    }
    if (data.job) showJob(data.job);
    if (data.sample) showSample(data.sample);
    if (data.status) { state.status = data.status; pagination.job.page = 1; renderJobs(); }
    if (data.species) { state.species = data.species; pagination.sample.page = 1; renderSamples(); }
    if (data.pageStep && pagination[data.list] && !button.disabled) {
      const paging = pagination[data.list];
      paging.page += Number(data.pageStep);
      paging.render();
      if (button.disabled) $(`${data.list}-pagination`).querySelector("button:not(:disabled)")?.focus({ preventScroll: true });
    }
    if (data.dataset) {
      state.dataset = data.dataset;
      renderData();
      $("dataset-list").querySelector('[aria-pressed="true"]').focus({ preventScroll: true });
    }
    if (data.relatedDataset) {
      $("detail-dialog").close();
      state.dataset = data.relatedDataset;
      pagination.data.page = Math.floor(datasets.findIndex((item) => item.id === state.dataset) / pagination.data.size) + 1;
      state.dataSearch = "";
      $("data-search").value = "";
      renderData();
      if (location.hash !== "#data") location.hash = "data";
      else $("main").focus({ preventScroll: true });
    }
    if (data.copyPath) {
      const item = datasets.find((item) => item.id === data.copyPath);
      if (!item) return;
      try {
        await navigator.clipboard.writeText(item.path);
        notify("예시 경로를 복사했습니다.");
      } catch {
        const code = $("dataset-detail").querySelector(".path-block code");
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(code);
        selection.removeAllRanges(); selection.addRange(range);
        notify("경로를 선택했습니다. Ctrl+C 또는 길게 눌러 복사하세요.");
      }
    }
  });

  const input = (id, key, list) => $(id).addEventListener("input", (event) => {
    state[key] = event.target.value;
    pagination[list].page = 1;
    pagination[list].render();
  });
  input("job-search", "jobSearch", "job");
  input("server-filter", "server", "job");
  input("sample-search", "sampleSearch", "sample");
  input("sample-type", "type", "sample");
  input("data-search", "dataSearch", "data");
  document.addEventListener("change", (event) => {
    const list = event.target.dataset.pageSize;
    const size = Number(event.target.value);
    if (!pagination[list] || ![5, 10, 25].includes(size)) return;
    pagination[list].size = size;
    pagination[list].page = 1;
    pagination[list].render();
  });
  $("detail-close").addEventListener("click", () => $("detail-dialog").close());
  $("preview-about").addEventListener("click", () => $("about-dialog").showModal());
  window.addEventListener("hashchange", showPage);
  renderServers(); renderJobs(); renderSpecies(); renderSamples(); renderData(); renderAccess(); renderTokenLink(); showPage();
})();
