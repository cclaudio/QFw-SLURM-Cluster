(function () {
  "use strict";

  const WORKFLOW_ID = "qfw-slurm-cluster";
  const IDENTITIES = ["user-a", "user-b", "user-c", "root"];
  const WIDGETS = [
    ["health", "Cluster health"],
    ["inventory", "Version and configuration inventory"],
    ["cluster-control", "Cluster control"],
    ["service-control", "Service control"],
    ["node-control", "Node control"],
    ["cluster-access", "Cluster access"],
    ["nodes", "Nodes"],
    ["services", "Services"],
    ["allocations", "Allocations"],
    ["experiments", "Experiments"],
    ["topology", "Topology"],
    ["results", "Result summary"],
    ["alerts", "Alerts"],
  ];
  const NON_FILTERABLE_WIDGETS = new Set([
    "cluster-control", "service-control", "node-control", "cluster-access",
  ]);
  let runtimeApi = null;
  let activeIdentity = "user-a";
  let state = { health: "unavailable", sources: {}, operations: [], experiments: [] };
  let polling = null;
  let eventPolling = null;
  let eventCursor = 0;
  const logCursors = {};
  let progressEvents = [];
  let progressIdentity = "user-a";
  let progressPaused = false;
  let dashboardRoot = null;
  let originalStatusOutput = null;
  let widgetChannel = null;
  let widgetStates = {};
  const popupWindows = new Map();
  const notifiedTerminal = new Set();

  function contextId() {
    return String(runtimeApi?.state.contextId || "detached");
  }

  function storageKey() {
    return `qfw.dashboard.v1.${contextId()}`;
  }

  function loadPresentation() {
    try {
      const value = JSON.parse(window.localStorage.getItem(storageKey()) || "{}");
      activeIdentity = IDENTITIES.includes(value.identity) ? value.identity : "user-a";
      widgetStates = value.widgets && typeof value.widgets === "object"
        ? value.widgets : {};
    } catch (error) {
      activeIdentity = "user-a";
      widgetStates = {};
    }
  }

  function savePresentation() {
    window.localStorage.setItem(storageKey(), JSON.stringify({
      identity: activeIdentity,
      widgets: widgetStates,
    }));
  }

  function requestUrl(path) {
    return runtimeApi.http.contextUrl(path);
  }

  async function request(path, options = {}) {
    const response = await runtimeApi.http.fetch(requestUrl(path), {
      cache: "no-store",
      headers: { "content-type": "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({ error: "invalid response" }));
    if (!response.ok) {
      throw new Error(payload.error?.message || payload.error
        || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function selectedSource(name) {
    return state.sources?.[name] || {
      name, status: "unavailable", records: [], error: "source has not been observed",
    };
  }

  function element(name, className = "", content = "") {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (content !== "") node.append(document.createTextNode(String(content ?? "")));
    return node;
  }

  function table(records, columns) {
    const wrapper = element("div", "qfw-table-wrap");
    const value = element("table", "qfw-table");
    const head = element("thead");
    const header = element("tr");
    let sortedRecords = [...records];
    columns.forEach(([key, label]) => {
      const heading = element("th");
      const sort = element("button", "qfw-table-sort", label);
      sort.type = "button";
      sort.addEventListener("click", () => {
        sortedRecords.sort((left, right) => String(left[key] ?? "")
          .localeCompare(String(right[key] ?? ""), undefined, { numeric: true }));
        renderRows();
      });
      heading.append(sort);
      header.append(heading);
    });
    head.append(header);
    const body = element("tbody");
    function renderRows() {
      body.replaceChildren();
      sortedRecords.forEach((record) => {
        const row = element("tr");
        columns.forEach(([key]) => row.append(element("td", "", record[key] ?? "—")));
        body.append(row);
      });
      if (!sortedRecords.length) {
        const row = element("tr");
        const cell = element("td", "qfw-empty", "No records");
        cell.colSpan = columns.length;
        row.append(cell);
        body.append(row);
      }
    }
    renderRows();
    value.append(head, body);
    wrapper.append(value);
    return wrapper;
  }

  function widgetPayload(id) {
    const docker = selectedSource("docker");
    const slurm = selectedSource("slurm");
    const services = selectedSource("services");
    const servicePlane = selectedSource("service-plane");
    const allocations = selectedSource("allocations");
    if (id === "health") {
      return {
        health: state.health,
        observed_at: state.observed_at,
        sources: Object.values(state.sources || {}).map((source) => ({
          name: source.name,
          status: source.status,
          observed_at: source.observed_at,
          error: source.error,
        })),
      };
    }
    if (id === "inventory") return selectedSource("inventory").records;
    if (id === "nodes") return slurm.records.filter((item) => item.kind === "node");
    if (id === "services") return services.records;
    if (id === "allocations") {
      return [
        ...slurm.records.filter((item) => item.kind === "job"),
        ...allocations.records,
      ];
    }
    if (id === "experiments") return state.experiments || [];
    if (id === "results") {
      return (state.experiments || []).filter((item) => item.result || item.completed_at);
    }
    if (id === "alerts") {
      return Object.values(state.sources || {})
        .filter((source) => source.status !== "ready")
        .map((source) => ({
          component: source.name,
          state: source.status,
          detail: source.error || "not ready",
        }));
    }
    if (id === "topology") {
      return {
        containers: docker.records,
        nodes: slurm.records.filter((item) => item.kind === "node"),
        allocations: [
          ...slurm.records.filter((item) => item.kind === "job"),
          ...allocations.records,
        ],
        services: services.records,
        service_plane: servicePlane.records,
        experiments: state.experiments || [],
      };
    }
    return {};
  }

  function renderHealth(payload) {
    const root = element("div", "qfw-health");
    root.append(element("strong", `qfw-state qfw-${payload.health}`, payload.health));
    root.append(element("span", "qfw-freshness", payload.observed_at || "not observed"));
    root.append(table(payload.sources || [], [
      ["name", "Source"], ["status", "State"], ["observed_at", "Observed"],
    ]));
    return root;
  }

  function renderAlerts(payload) {
    const root = element("div", "qfw-alerts");
    if (!payload.length) {
      root.append(element("p", "qfw-empty", "No active alerts"));
      return root;
    }
    payload.forEach((alert) => {
      const panel = element("article", "qfw-alert");
      const header = element("header", "qfw-alert-header");
      header.append(
        element("strong", "qfw-alert-component", alert.component || "unknown component"),
        element("span", `qfw-state qfw-${alert.state || "unavailable"}`,
          alert.state || "unavailable"),
      );
      const detail = element("pre", "qfw-alert-detail");
      detail.textContent = typeof alert.detail === "string"
        ? alert.detail : JSON.stringify(alert.detail, null, 2);
      panel.append(header, detail);
      root.append(panel);
    });
    return root;
  }

  function renderTopology(payload) {
    const root = element("div", "qfw-topology");
    const controls = element("div", "qfw-inline-controls");
    const projection = element("select");
    projection.className = "qfw-topology-projection";
    ["Cluster", "Experiment"].forEach((label) => {
      const option = element("option", "", label);
      option.value = label.toLowerCase();
      projection.append(option);
    });
    projection.value = widgetStates.topology?.projection || "cluster";
    projection.selectedOptions[0]?.setAttribute("selected", "selected");
    projection.addEventListener("change", () => {
      widgetStates.topology = { ...widgetStates.topology, projection: projection.value };
      savePresentation();
      renderDashboard();
      publishWidgets();
    });
    const filter = element("input");
    filter.className = "qfw-topology-filter";
    filter.placeholder = "filter objects";
    filter.value = widgetStates.topology?.filter || "";
    filter.setAttribute("value", filter.value);
    const zoom = element("input");
    zoom.className = "qfw-topology-zoom";
    zoom.type = "range";
    zoom.min = "60";
    zoom.max = "180";
    zoom.value = String(widgetStates.topology?.zoom || 100);
    zoom.setAttribute("value", zoom.value);
    const projectionLabel = element("label", "qfw-control-group", "Projection ");
    projectionLabel.append(projection);
    const filterLabel = element("label", "qfw-control-group", "Filter ");
    filterLabel.append(filter);
    const zoomControlLabel = element("label", "qfw-control-group", "Zoom ");
    zoomControlLabel.append(zoom);
    controls.append(projectionLabel, filterLabel, zoomControlLabel);
    root.append(controls);
    const objects = projection.value === "cluster"
      ? [
          { type: "controller", id: "slurmctld", state: "registered" },
          ...[...new Set((payload.nodes || []).map((item) => item.partition))]
            .filter(Boolean).map((id) => ({
              type: "partition", id, state: "configured", parent: "slurmctld",
            })),
          ...(payload.nodes || []).map((item) => ({
            type: "node", id: item.node, state: item.state, parent: item.partition,
          })),
          ...(payload.services || []).map((item) => ({
            type: "service",
            id: item.service_id || item.name,
            state: item.state || item.status,
            parent: item.node || "slurmctld",
          })),
          ...(payload.service_plane || []).map((item) => ({
            type: item.component === "dvm" ? "dvm" : item.component,
            id: item.component,
            state: item.state,
            parent: item.component === "dvm" ? "nwqsim" : "slurmctld",
          })),
        ]
      : (payload.experiments || []).flatMap((item) => {
          const jobs = (payload.allocations || []).filter((job) =>
            String(job.job_id || "").split("+")[0] === String(item.slurm_job_id));
          const records = item.result?.records || [];
          const slurmRecords = item.result?.slurm_records || [];
          const providerJobs = records.flatMap((record) => {
            const identifier = record.provider_job_id || record.iqm_job_id
              || record.details?.provider_job_id;
            return identifier ? [{
              type: "provider-job", id: String(identifier), state: record.status,
              parent: item.slurm_job_id,
            }] : [];
          });
          return [
            { type: "experiment", id: item.experiment_id, state: item.status },
            {
              type: "job", id: item.slurm_job_id, state: item.status,
              parent: item.experiment_id,
            },
            ...jobs.map((job) => ({
              type: String(job.job_id).includes("+") ? "heterogeneous-group" : "job-state",
              id: String(job.job_id), state: job.state, parent: item.slurm_job_id,
            })),
            ...jobs.filter((job) => job.nodes).map((job) => ({
              type: "allocated-nodes", id: `${job.job_id}:${job.nodes}`,
              state: job.state, parent: String(job.job_id),
            })),
            ...slurmRecords.map((record) => ({
              type: record.kind === "step" ? "job-step" : "accounting-job",
              id: String(record.job_id), state: record.state,
              parent: item.slurm_job_id,
            })),
            ...(item.reservations || []).map((entry) => ({
              type: "reservation", id: entry.join(":"), state: item.status,
              parent: item.slurm_job_id,
            })),
            ...providerJobs,
            ...(item.result && Object.keys(item.result).length ? [{
              type: "result", id: `${item.experiment_id}:result`,
              state: item.status, parent: item.experiment_id,
            }] : []),
            ...(item.artifacts || []).map((path, index) => ({
              type: "artifact", id: `${item.experiment_id}:artifact:${index}`,
              path, state: "retained", parent: `${item.experiment_id}:result`,
            })),
          ];
        }).filter((item) => item.id);
    objects.sort((left, right) => `${left.type}:${left.id}`
      .localeCompare(`${right.type}:${right.id}`));
    const visibleObjects = objects.filter((item) => !filter.value
      || JSON.stringify(item).toLowerCase().includes(filter.value.toLowerCase()));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("qfw-topology-graph");
    svg.setAttribute("viewBox", "0 0 900 500");
    svg.style.width = `${Number(zoom.value)}%`;
    const positions = new Map(visibleObjects.map((item, index) => [item.id, {
      x: 35 + (index % 4) * 215,
      y: 35 + Math.floor(index / 4) * 100,
    }]));
    visibleObjects.forEach((item) => {
      const source = positions.get(item.parent);
      const target = positions.get(item.id);
      if (!source || !target) return;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(source.x + 92));
      line.setAttribute("y1", String(source.y + 32));
      line.setAttribute("x2", String(target.x + 92));
      line.setAttribute("y2", String(target.y + 32));
      line.setAttribute("class", "qfw-topology-edge");
      svg.append(line);
    });
    visibleObjects.forEach((item, index) => {
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const column = index % 4;
      const row = Math.floor(index / 4);
      group.setAttribute("transform", `translate(${35 + column * 215} ${35 + row * 100})`);
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "button");
      group.dataset.objectId = item.id;
      const box = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      box.setAttribute("width", "185");
      box.setAttribute("height", "64");
      box.setAttribute("rx", "8");
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", "10");
      label.setAttribute("y", "25");
      label.textContent = `${item.type}: ${item.id}`;
      const status = document.createElementNS("http://www.w3.org/2000/svg", "text");
      status.setAttribute("x", "10");
      status.setAttribute("y", "48");
      status.textContent = item.state || "unknown";
      const select = () => {
        widgetStates.topology = { ...widgetStates.topology, selected: item.id };
        savePresentation();
        const component = runtimeApi.elements.progressOutputPane
          .querySelector("[data-qfw-filter=component]");
        const contextFilter = runtimeApi.elements.progressOutputPane
          .querySelector("[data-qfw-filter=context]");
        if (component) {
          const mapping = {
            "job": "slurm", "job-state": "slurm", "job-step": "slurm",
            "accounting-job": "slurm", "service": "qpmd",
            "directory": "directory", "gateway": "gateway", "dvm": "dvm",
            "provider-job": "provider", "experiment": "application",
            "result": "application", "artifact": "application",
          };
          component.value = mapping[item.type] || "";
          component.dispatchEvent(new Event("change"));
        }
        if (contextFilter && [...contextFilter.options]
          .some((option) => option.value === item.id)) {
          contextFilter.value = item.id;
          renderProgress();
        }
        if (item.type === "result" || item.type === "artifact") {
          const resultWidget = dashboardRoot.querySelector("[data-widget=results]");
          if (resultWidget) {
            resultWidget.open = true;
            resultWidget.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        }
        renderDashboard();
        publishWidgets();
      };
      group.addEventListener("click", select);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") select();
      });
      group.append(box, label, status);
      svg.append(group);
    });
    filter.addEventListener("input", () => {
      widgetStates.topology = { ...widgetStates.topology, filter: filter.value };
      savePresentation();
      renderDashboard();
      publishWidgets();
    });
    zoom.addEventListener("input", () => {
      widgetStates.topology = { ...widgetStates.topology, zoom: Number(zoom.value) };
      svg.style.width = `${zoom.value}%`;
      savePresentation();
      publishWidgets();
    });
    root.append(svg, table(visibleObjects, [
      ["type", "Object"], ["id", "Identifier"], ["state", "State"],
    ]));
    const selected = visibleObjects.find((item) =>
      item.id === widgetStates.topology?.selected);
    if (selected) {
      root.append(element("h4", "", "Selected object"));
      root.append(element("pre", "qfw-topology-selection",
        JSON.stringify(selected, null, 2)));
    }
    return root;
  }

  function renderWidgetBody(id, payload) {
    if (id === "cluster-control") return renderClusterControl();
    if (id === "service-control") return renderServiceControl();
    if (id === "node-control") return renderNodeControl();
    if (id === "cluster-access") return renderClusterAccess();
    const query = String(widgetStates[id]?.filter || "").toLowerCase();
    if (query && Array.isArray(payload)) {
      payload = payload.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    }
    if (id === "health") return renderHealth(payload);
    if (id === "inventory") {
      return table(payload, [
        ["kind", "Kind"], ["component", "Component"],
        ["value", "Version, revision, or fingerprint"], ["path", "Path"],
      ]);
    }
    if (id === "nodes") {
      return table(payload, [
        ["node", "Node"], ["partition", "Partition"], ["state", "State"],
        ["cpus", "CPUs"], ["memory", "Memory"], ["reason", "Reason"],
      ]);
    }
    if (id === "services") {
      return table(payload, [
        ["service_id", "Service"], ["node", "Node"], ["state", "State"],
        ["backend", "Backend"], ["active_reservations", "Reservations"],
      ]);
    }
    if (id === "allocations") {
      return table(payload, [
        ["job_id", "Job"], ["user", "User"], ["state", "State"],
        ["partition", "Partition"], ["nodes", "Nodes"], ["reason", "Reason"],
      ]);
    }
    if (id === "experiments" || id === "results") {
      const root = element("div");
      root.append(table(payload, [
        ["experiment_id", "Experiment"], ["identity", "User"],
        ["backend", "Backend"], ["example", "Example"], ["status", "State"],
        ["slurm_job_id", "Job"],
      ]));
      const selection = [];
      payload.forEach((experiment) => {
        const controls = element("div", "qfw-inline-controls");
        controls.append(element("code", "", experiment.experiment_id));
        if (!experiment.completed_at && experiment.slurm_job_id) {
          const cancel = element("button", "danger", "Cancel");
          cancel.type = "button";
          cancel.addEventListener("click", async () => {
            if (!window.confirm(`Cancel ${experiment.experiment_id}?`)) return;
            await request("/api/qfw-dashboard/experiments/cancel", {
              method: "POST", body: JSON.stringify({
                experiment_id: experiment.experiment_id, identity: activeIdentity,
              }),
            });
          });
          controls.append(cancel);
        }
        if (experiment.completed_at) {
          const details = element("details", "qfw-result-details");
          details.append(
            element("summary", "", "Result and reproducibility manifest"),
            element("pre", "", JSON.stringify({
              result: experiment.result,
              reservations: experiment.reservations,
              artifacts: experiment.artifacts,
              manifest: experiment.manifest,
            }, null, 2)),
          );
          controls.append(details);
          (experiment.artifacts || []).forEach((path, index) => {
            const download = element("button", "", `Download ${path.split("/").pop()}`);
            download.type = "button";
            download.addEventListener("click", async () => {
              try {
                const payload = await request(
                  "/api/qfw-dashboard/artifact"
                    + `?experiment_id=${encodeURIComponent(experiment.experiment_id)}`
                    + `&index=${index}&identity=${encodeURIComponent(activeIdentity)}`,
                );
                const bytes = Uint8Array.from(atob(payload.content_base64),
                  (character) => character.charCodeAt(0));
                const anchor = document.createElement("a");
                anchor.href = URL.createObjectURL(new Blob([bytes]));
                anchor.download = payload.name;
                anchor.click();
                URL.revokeObjectURL(anchor.href);
              } catch (error) {
                window.alert(error.message);
              }
            });
            controls.append(download);
          });
          const retry = element("button", "", "Retry");
          retry.type = "button";
          retry.addEventListener("click", async () => {
            const hardware = experiment.backend === "iqm";
            if (hardware && !window.confirm("Retry bounded work on real IQM hardware?")) return;
            await request("/api/qfw-dashboard/experiments/retry", {
              method: "POST", body: JSON.stringify({
                experiment_id: experiment.experiment_id,
                identity: activeIdentity,
                submit_real_hardware: hardware,
              }),
            });
          });
          controls.append(retry);
          if (id === "results") {
            const select = element("input");
            select.type = "checkbox";
            select.addEventListener("change", () => {
              if (select.checked) selection.push(experiment.experiment_id);
              else selection.splice(selection.indexOf(experiment.experiment_id), 1);
            });
            controls.append(element("label", "", " Compare "), select);
          }
        }
        root.append(controls);
      });
      if (id === "results") {
        const compare = element("button", "", "Compare selected results");
        compare.type = "button";
        const output = element("pre");
        compare.addEventListener("click", async () => {
          try {
            const value = await request("/api/qfw-dashboard/results/compare", {
              method: "POST", body: JSON.stringify({
                experiment_ids: selection, identity: activeIdentity,
              }),
            });
            output.textContent = JSON.stringify(value, null, 2);
          } catch (error) {
            window.alert(error.message);
          }
        });
        root.append(compare, output);
      }
      return root;
    }
    if (id === "alerts") {
      return renderAlerts(payload);
    }
    if (id === "topology") return renderTopology(payload);
    return element("pre", "", JSON.stringify(payload, null, 2));
  }

  function openWidget(id, label) {
    const instanceId = `${contextId()}:${id}`;
    const existing = popupWindows.get(instanceId);
    if (existing && !existing.closed) {
      existing.focus();
      return;
    }
    const parameters = new URLSearchParams({
      widget: id, label, instance_id: instanceId, context_id: contextId(),
    });
    const popup = window.open(
      `/qfw-dashboard/widget?${parameters.toString()}`,
      `qfw-widget-${instanceId.replace(/[^A-Za-z0-9_-]/g, "-")}`,
      "popup=yes,width=1000,height=720,resizable=yes,scrollbars=yes",
    );
    if (!popup) {
      runtimeApi.notifications.appendOutput("dashboard widget popup was blocked\n", "error");
      return;
    }
    popupWindows.set(instanceId, popup);
    window.setTimeout(publishWidgets, 100);
  }

  function buildWidget(id, label) {
    const details = element("details", "qfw-widget");
    let pulseHash = 0;
    for (const character of id) {
      pulseHash = ((pulseHash * 33) + character.charCodeAt(0)) >>> 0;
    }
    const pulseDuration = 10000 + (pulseHash % 6000);
    const pulsePhase = (Date.now() + (pulseHash * 104729)) % pulseDuration;
    details.style.setProperty("--qfw-pulse-duration", `${pulseDuration}ms`);
    details.style.setProperty("--qfw-pulse-delay", `${-pulsePhase}ms`);
    details.dataset.widget = id;
    details.open = widgetStates[id]?.expanded !== false;
    const summary = element("summary");
    const chevron = element("span", "qfw-widget-chevron", "›");
    chevron.setAttribute("aria-hidden", "true");
    summary.append(chevron);
    summary.append(element("span", "qfw-widget-title", label));
    const filter = element("input", "qfw-widget-filter");
    filter.type = "search";
    filter.placeholder = "filter";
    filter.value = widgetStates[id]?.filter || "";
    filter.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    filter.addEventListener("input", (event) => {
      event.preventDefault();
      event.stopPropagation();
      widgetStates[id] = { ...widgetStates[id], filter: filter.value };
      savePresentation();
      const body = details.children[1];
      body.replaceWith(renderWidgetBody(id, widgetPayload(id)));
      publishWidgets();
    });
    const pop = element("button", "qfw-popout", "Pop out");
    pop.type = "button";
    pop.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openWidget(id, label);
    });
    if (!NON_FILTERABLE_WIDGETS.has(id)) summary.append(filter);
    summary.append(pop);
    details.append(summary, renderWidgetBody(id, widgetPayload(id)));
    details.addEventListener("toggle", () => {
      widgetStates[id] = { ...widgetStates[id], expanded: details.open };
      savePresentation();
      publishWidgets();
    });
    return details;
  }

  function canvasCamera() {
    const saved = widgetStates.canvas || {};
    return {
      x: Number.isFinite(Number(saved.x)) ? Number(saved.x) : 32,
      y: Number.isFinite(Number(saved.y)) ? Number(saved.y) : 32,
      scale: Math.min(2.25, Math.max(0.35,
        Number.isFinite(Number(saved.scale)) ? Number(saved.scale) : 1)),
    };
  }

  function saveCanvasCamera(camera) {
    widgetStates.canvas = { x: camera.x, y: camera.y, scale: camera.scale };
    savePresentation();
  }

  function installCanvasInteraction(viewport, stage, zoomLabel) {
    const camera = canvasCamera();
    let pan = null;

    function apply() {
      stage.style.left = `${camera.x / camera.scale}px`;
      stage.style.top = `${camera.y / camera.scale}px`;
      stage.style.zoom = String(camera.scale);
      zoomLabel.textContent = `${Math.round(camera.scale * 100)}%`;
    }

    function zoomAt(clientX, clientY, factor) {
      const bounds = viewport.getBoundingClientRect();
      const localX = clientX - bounds.left;
      const localY = clientY - bounds.top;
      const previous = camera.scale;
      const next = Math.min(2.25, Math.max(0.35, previous * factor));
      const worldX = (localX - camera.x) / previous;
      const worldY = (localY - camera.y) / previous;
      camera.scale = next;
      camera.x = localX - worldX * next;
      camera.y = localY - worldY * next;
      apply();
      saveCanvasCamera(camera);
    }

    viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, Math.exp(-event.deltaY * 0.0015));
    }, { passive: false });
    viewport.addEventListener("pointerdown", (event) => {
      if (event.button !== 1) return;
      event.preventDefault();
      pan = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
      viewport.setPointerCapture(event.pointerId);
      viewport.classList.add("is-panning");
    });
    viewport.addEventListener("pointermove", (event) => {
      if (!pan || pan.pointerId !== event.pointerId) return;
      camera.x += event.clientX - pan.x;
      camera.y += event.clientY - pan.y;
      pan.x = event.clientX;
      pan.y = event.clientY;
      apply();
    });
    function finishPan(event) {
      if (!pan || pan.pointerId !== event.pointerId) return;
      pan = null;
      viewport.classList.remove("is-panning");
      saveCanvasCamera(camera);
    }
    viewport.addEventListener("pointerup", finishPan);
    viewport.addEventListener("pointercancel", finishPan);
    viewport.addEventListener("auxclick", (event) => {
      if (event.button === 1) event.preventDefault();
    });
    apply();

    return {
      reset() {
        camera.x = 32;
        camera.y = 32;
        camera.scale = 1;
        apply();
        saveCanvasCamera(camera);
      },
      zoom(factor) {
        const bounds = viewport.getBoundingClientRect();
        zoomAt(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2, factor);
      },
    };
  }

  const selectedOperations = {};

  function controlValue(widget, name, fallback) {
    return widgetStates[widget]?.[name] ?? fallback;
  }

  function rememberControl(widget, name, value) {
    widgetStates[widget] = { ...widgetStates[widget], [name]: value };
    savePresentation();
    publishWidgets();
  }

  function selectControl(widget, name, choices, fallback) {
    const control = element("select");
    control.dataset.qfwControl = name;
    choices.forEach(([value, label]) => {
      const option = element("option", "", label);
      option.value = value;
      control.append(option);
    });
    control.value = controlValue(widget, name, fallback);
    control.selectedOptions[0]?.setAttribute("selected", "selected");
    control.addEventListener("change", () => {
      rememberControl(widget, name, control.value);
    });
    return control;
  }

  function textControl(widget, name, fallback, placeholder) {
    const control = element("input");
    control.dataset.qfwControl = name;
    control.placeholder = placeholder;
    control.value = controlValue(widget, name, fallback);
    control.setAttribute("value", control.value);
    control.addEventListener("input", () => {
      rememberControl(widget, name, control.value);
    });
    return control;
  }

  function operationField(label, control) {
    const field = element("label", "qfw-operation-field");
    field.append(element("span", "", label), control);
    return field;
  }

  function operationForGroup(group) {
    const matching = (state.operations || []).filter((item) => {
      if (group === "cluster") return item.action?.startsWith("cluster-");
      if (group === "services") return item.action?.startsWith("service-");
      return item.action?.startsWith("node-");
    }).sort((left, right) => String(right.created_at)
      .localeCompare(String(left.created_at)));
    return matching.find((item) => item.operation_id === selectedOperations[group])
      || matching[0] || null;
  }

  async function runOperation(group, payload) {
    const response = await request("/api/qfw-dashboard/operations", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        identity: activeIdentity,
        request_id: window.crypto.randomUUID(),
      }),
    });
    selectedOperations[group] = response.operation_id;
    await refreshState();
  }

  async function abortOperation(group) {
    const operation = operationForGroup(group);
    if (!operation) return;
    await request("/api/qfw-dashboard/operations/abort", {
      method: "POST",
      body: JSON.stringify({
        operation_id: operation.operation_id,
        identity: activeIdentity,
      }),
    });
    await refreshState();
  }

  function operationOutput(group) {
    const operation = operationForGroup(group);
    const output = element("pre", "qfw-operation-output");
    if (!operation) {
      output.textContent = "No operation has run in this group.";
      return output;
    }
    const heading = [
      `${operation.status.toUpperCase()} · ${operation.action}`,
      `target=${operation.target} operation=${operation.operation_id}`,
    ];
    output.textContent = [...heading, ...(operation.output || [])].join("\n");
    output.scrollTop = output.scrollHeight;
    return output;
  }

  function operationButtons(widget, group, submit) {
    const buttons = element("div", "qfw-operation-buttons");
    const run = element("button", "qfw-operation-run", "Run");
    run.type = "button";
    run.dataset.qfwAction = "run";
    run.dataset.qfwWidget = widget;
    run.disabled = activeIdentity !== "root";
    run.addEventListener("click", async () => {
      try {
        await submit();
      } catch (error) {
        window.alert(error.message);
      }
    });
    const abort = element("button", "danger", "Abort");
    abort.type = "button";
    abort.dataset.qfwAction = "abort";
    abort.dataset.qfwWidget = widget;
    const operation = operationForGroup(group);
    abort.disabled = activeIdentity !== "root"
      || !operation || !["queued", "running", "aborting"].includes(operation.status);
    abort.addEventListener("click", async () => {
      if (!window.confirm(`Abort ${operation?.action || "operation"}?`)) return;
      try {
        await abortOperation(group);
      } catch (error) {
        window.alert(error.message);
      }
    });
    buttons.append(run, abort);
    return buttons;
  }

  function renderClusterControl() {
    const widget = "cluster-control";
    const cluster = element("div", "qfw-operation-control qfw-operation-cluster");
    const clusterAction = selectControl(widget, "operation", [
      ["start", "Start"], ["stop", "Stop"], ["restart", "Restart"],
      ["recreate", "Recreate"],
    ], "start");
    cluster.append(
      operationField("Operation", clusterAction),
      operationButtons(widget, "cluster", async () => {
        const operation = clusterAction.value;
        const consequence = operation === "recreate"
          ? " This removes and recreates containers and named volumes." : "";
        if (!window.confirm(`${operation} cluster as root?${consequence}`)) return;
        await runOperation("cluster", { action: `cluster-${operation}`, target: "cluster" });
      }),
      operationOutput("cluster"),
    );
    return cluster;
  }

  function renderServiceControl() {
    const widget = "service-control";
    const services = element("div", "qfw-operation-control qfw-operation-services");
    const serviceTarget = selectControl(widget, "target", [
      ["all", "All services"], ["directory", "Directory"],
      ["nwqsim", "NWQSim"], ["iqm", "IQM"], ["gateway", "Gateway"],
    ], "all");
    const serviceAction = selectControl(widget, "operation", [
      ["start", "Start"], ["stop", "Stop"], ["restart", "Restart"],
      ["recover", "Recover"],
    ], "start");
    services.append(
      operationField("Target", serviceTarget),
      operationField("Operation", serviceAction),
      operationButtons(widget, "services", async () => {
        if (!window.confirm(
          `${serviceAction.value} ${serviceTarget.value} as root?`,
        )) return;
        await runOperation("services", {
          action: `service-${serviceAction.value}`,
          target: serviceTarget.value,
        });
      }),
      operationOutput("services"),
    );
    return services;
  }

  function renderNodeControl() {
    const widget = "node-control";
    const nodes = element("div", "qfw-operation-control qfw-operation-nodes");
    const node = textControl(widget, "node", "", "node name");
    const nodeAction = selectControl(widget, "operation", [
      ["drain", "Drain"], ["resume", "Resume"],
    ], "drain");
    const reason = textControl(widget, "reason", "qfw-dashboard", "drain reason");
    nodes.append(
      operationField("Node", node),
      operationField("Operation", nodeAction),
      operationField("Reason", reason),
      operationButtons(widget, "nodes", async () => {
        if (!window.confirm(`${nodeAction.value} ${node.value} as root?`)) return;
        await runOperation("nodes", {
          action: `node-${nodeAction.value}`,
          target: node.value,
          reason: reason.value,
        });
      }),
      operationOutput("nodes"),
    );
    return nodes;
  }

  async function openClusterShell(target, confirmRoot = true) {
    if (confirmRoot && activeIdentity === "root"
        && !window.confirm(`Open a root shell in ${target}?`)) return;
    await request("/api/qfw-dashboard/shell", {
      method: "POST",
      body: JSON.stringify({ identity: activeIdentity, target }),
    });
    runtimeApi.layout.ensurePane("shell", "status", "column");
    window.ElectroBoyFrontend.invokeModule("project-shell", "connectProjectShellEvents");
  }

  function renderClusterAccess() {
    const widget = "cluster-access";
    const access = element("div", "qfw-access-control");
    const shellTarget = element("select");
    shellTarget.dataset.qfwControl = "node";
    ["slurmctld", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8",
      "nwqsim-head", "nwqsim-worker-1", "nwqsim-worker-2", "iqm-head"]
      .forEach((name) => {
        const option = element("option", "", name);
        option.value = name;
        option.disabled = activeIdentity !== "root"
          && (name.startsWith("nwqsim-") || name === "iqm-head");
        shellTarget.append(option);
      });
    shellTarget.value = controlValue(widget, "node", "slurmctld");
    shellTarget.selectedOptions[0]?.setAttribute("selected", "selected");
    shellTarget.addEventListener("change", () => {
      rememberControl(widget, "node", shellTarget.value);
    });
    const shell = element("button", "", "Open cluster shell");
    shell.type = "button";
    shell.dataset.qfwAction = "open-shell";
    shell.dataset.qfwWidget = widget;
    shell.addEventListener("click", async () => {
      try {
        await openClusterShell(shellTarget.value);
      } catch (error) {
        window.alert(error.message);
      }
    });
    access.append(operationField("Node", shellTarget), shell);
    return access;
  }

  function renderExperimentForm(root) {
    const form = element("form", "qfw-experiment-form");
    form.append(element("h3", "", "Submit experiment"));
    const backend = element("select");
    ["nwqsim", "iqm"].forEach((name) => {
      const option = element("option", "", name);
      option.value = name;
      backend.append(option);
    });
    const example = element("select");
    const discoveredExamples = state.examples?.length
      ? state.examples : [{ name: "qiskit-simple", backends: ["nwqsim", "iqm"] }];
    discoveredExamples.forEach((record) => {
      const option = element("option", "", record.name);
      option.value = record.name;
      option.dataset.backends = (record.backends || []).join(",");
      example.append(option);
    });
    example.value = "qiskit-simple";
    const mode = element("select");
    ["normal", "heterogeneous"].forEach((name) => {
      const option = element("option", "", name);
      option.value = name;
      mode.append(option);
    });
    const shots = element("input");
    shots.type = "number";
    shots.min = "1";
    shots.value = "16";
    const timeMinutes = element("input");
    timeMinutes.type = "number";
    timeMinutes.min = "1";
    timeMinutes.max = "240";
    timeMinutes.value = "45";
    const chemistryApp = element("input");
    chemistryApp.placeholder = "/workspace/.../chemistry.py";
    const classical = {};
    [
      ["partition", "Partition", "normal", "text"],
      ["nodes", "Application nodes", 1, "number"],
      ["tasks", "Application tasks", 1, "number"],
      ["launcher_nodes", "Launcher nodes", 1, "number"],
      ["launcher_tasks", "Launcher tasks", 1, "number"],
      ["account", "Account", "", "text"],
      ["qos", "QoS", "", "text"],
    ].forEach(([name, label, initial, type]) => {
      const input = element("input");
      input.type = type;
      if (type === "number") input.min = "1";
      input.value = String(initial);
      classical[name] = input;
      form.append(element("label", "", `${label} `), input);
    });
    function updateBackendConstraints() {
      timeMinutes.max = backend.value === "iqm" ? "15" : "240";
      if (backend.value === "iqm" && Number(timeMinutes.value) > 15) {
        timeMinutes.value = "15";
      }
      [...example.options].forEach((option) => {
        option.disabled = !String(option.dataset.backends || "")
          .split(",").includes(backend.value);
      });
      if (example.selectedOptions[0]?.disabled) {
        example.value = [...example.options]
          .find((option) => !option.disabled)?.value || "";
      }
    }
    backend.addEventListener("change", updateBackendConstraints);
    updateBackendConstraints();
    const requirements = {};
    [
      ["circ_count", "Circuits", 1],
      ["max_qubits", "Max qubits", 5],
      ["max_depth", "Max depth", 100],
      ["max_one_q_gates", "Max 1Q gates", 0],
      ["max_two_q_gates", "Max 2Q gates", 0],
      ["max_measurements", "Max measurements", 0],
    ].forEach(([name, label, initial]) => {
      const input = element("input");
      input.type = "number";
      input.min = name.startsWith("max_") && name !== "max_qubits"
        && name !== "max_depth" ? "0" : "1";
      input.value = String(initial);
      requirements[name] = input;
      form.append(element("label", "", `${label} `), input);
    });
    const workload = element("select");
    ["quantum", "hybrid"].forEach((name) => {
      const option = element("option", "", name);
      option.value = name;
      workload.append(option);
    });
    const submit = element("button", "", "Submit through Slurm");
    submit.type = "submit";
    const preview = element("button", "", "Preview allocation");
    preview.type = "button";
    const previewOutput = element("code", "qfw-command-preview");
    const preset = element("select");
    const presetKey = `${storageKey()}.experiment-presets`;
    function readPresets() {
      try {
        return JSON.parse(window.localStorage.getItem(presetKey) || "{}");
      } catch (error) {
        return {};
      }
    }
    function renderPresets() {
      preset.replaceChildren(element("option", "", "saved presets"));
      Object.keys(readPresets()).sort().forEach((name) => {
        const option = element("option", "", name);
        option.value = name;
        preset.append(option);
      });
    }
    function formPayload() {
      return {
        identity: activeIdentity,
        backend: backend.value,
        example: example.value,
        allocation_mode: mode.value,
        shots: Number(shots.value),
        time_minutes: Number(timeMinutes.value),
        chemistry_app: chemistryApp.value,
        workload_kind: workload.value,
        ...Object.fromEntries(
          Object.entries(classical).map(([name, input]) => [
            name, input.type === "number" ? Number(input.value) : input.value,
          ]),
        ),
        ...Object.fromEntries(
          Object.entries(requirements).map(([name, input]) => [
            name, Number(input.value),
          ]),
        ),
      };
    }
    function applyPreset(payload) {
      backend.value = payload.backend || "nwqsim";
      example.value = payload.example || "qiskit-simple";
      mode.value = payload.allocation_mode || "normal";
      workload.value = payload.workload_kind || "quantum";
      shots.value = String(payload.shots || 16);
      timeMinutes.value = String(payload.time_minutes || 45);
      chemistryApp.value = payload.chemistry_app || "";
      Object.entries(classical).forEach(([name, input]) => {
        if (payload[name] !== undefined) input.value = String(payload[name]);
      });
      Object.entries(requirements).forEach(([name, input]) => {
        if (payload[name] !== undefined) input.value = String(payload[name]);
      });
      updateBackendConstraints();
    }
    renderPresets();
    preset.addEventListener("change", () => {
      const payload = readPresets()[preset.value];
      if (payload) applyPreset(payload);
    });
    const savePreset = element("button", "", "Save preset");
    savePreset.type = "button";
    savePreset.addEventListener("click", () => {
      const name = window.prompt("Preset name");
      if (!name || !/^[A-Za-z0-9_.-]+$/.test(name)) return;
      const presets = readPresets();
      const payload = formPayload();
      delete payload.identity;
      presets[name] = payload;
      window.localStorage.setItem(presetKey, JSON.stringify(presets));
      renderPresets();
      preset.value = name;
    });
    form.append(
      element("label", "", "Backend "), backend,
      element("label", "", "Example "), example,
      element("label", "", "Allocation "), mode,
      element("label", "", "Workload "), workload,
      element("label", "", "Shots "), shots,
      element("label", "", "Time limit (minutes) "), timeMinutes,
      element("label", "", "Chemistry application "), chemistryApp,
      element("label", "", "Preset "), preset, savePreset,
      preview, submit, previewOutput,
    );
    preview.addEventListener("click", async () => {
      try {
        const payload = await request("/api/qfw-dashboard/preview", {
          method: "POST", body: JSON.stringify(formPayload()),
        });
        previewOutput.textContent = payload.command;
      } catch (error) {
        window.alert(error.message);
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const hardware = backend.value === "iqm";
      if (hardware && !window.confirm("Submit bounded work to real IQM hardware?")) return;
      try {
        await request("/api/qfw-dashboard/experiments", {
          method: "POST",
          body: JSON.stringify({
            ...formPayload(), submit_real_hardware: hardware,
          }),
        });
        await refreshState();
      } catch (error) {
        window.alert(error.message);
      }
    });
    root.append(form);
  }

  function captureWidgetScrollPositions() {
    const positions = {};
    if (!dashboardRoot) return positions;
    dashboardRoot.querySelectorAll(".qfw-widget[data-widget]").forEach((widget) => {
      positions[widget.dataset.widget] = [
        ...widget.querySelectorAll(
          ".qfw-topology, .qfw-table-wrap, .qfw-alert-detail",
        ),
      ].map((container) => ({
        left: container.scrollLeft,
        top: container.scrollTop,
      }));
    });
    return positions;
  }

  function restoreWidgetScrollPositions(positions) {
    Object.entries(positions).forEach(([id, offsets]) => {
      const widget = dashboardRoot?.querySelector(`[data-widget="${id}"]`);
      if (!widget) return;
      const containers = widget.querySelectorAll(
        ".qfw-topology, .qfw-table-wrap, .qfw-alert-detail",
      );
      offsets.forEach((offset, index) => {
        if (!containers[index]) return;
        containers[index].scrollLeft = offset.left;
        containers[index].scrollTop = offset.top;
      });
    });
  }

  function renderDashboard() {
    if (!dashboardRoot) return;
    const scrollPositions = captureWidgetScrollPositions();
    dashboardRoot.replaceChildren();
    const header = element("header", "qfw-dashboard-header");
    const title = element("div", "qfw-dashboard-title");
    title.append(
      element("span", "qfw-dashboard-eyebrow", "OPENQSE OPERATIONS"),
      element("h2", "", "QFw Slurm Cluster"),
    );
    header.append(title);
    const identity = element("select", "qfw-identity");
    IDENTITIES.forEach((name) => {
      const option = element("option", "", name);
      option.value = name;
      identity.append(option);
    });
    identity.value = activeIdentity;
    identity.addEventListener("change", () => {
      activeIdentity = identity.value;
      savePresentation();
      renderDashboard();
      publishWidgets();
    });
    const identityLabel = element("label", "qfw-identity-label", "Cluster identity ");
    identityLabel.append(identity);
    const viewControls = element("div", "qfw-view-controls");
    const zoomOut = element("button", "", "−");
    const zoomLabel = element("output", "qfw-zoom-label", "100%");
    const zoomIn = element("button", "", "+");
    const reset = element("button", "", "Reset view");
    [zoomOut, zoomIn, reset].forEach((button) => { button.type = "button"; });
    viewControls.append(
      element("span", "qfw-canvas-hint", "Wheel zoom · middle-drag pan"),
      zoomOut, zoomLabel, zoomIn, reset,
    );
    header.append(viewControls, identityLabel);
    dashboardRoot.append(header);
    const viewport = element("div", "qfw-canvas-viewport");
    viewport.setAttribute("aria-label", "Zoomable dashboard canvas");
    const stage = element("main", "qfw-canvas-stage");
    const grid = element("div", "qfw-widget-grid");
    WIDGETS.forEach(([id, label]) => grid.append(buildWidget(id, label)));
    stage.append(grid);
    renderExperimentForm(stage);
    viewport.append(stage);
    dashboardRoot.append(viewport);
    const camera = installCanvasInteraction(viewport, stage, zoomLabel);
    zoomOut.addEventListener("click", () => camera.zoom(0.85));
    zoomIn.addEventListener("click", () => camera.zoom(1 / 0.85));
    reset.addEventListener("click", () => camera.reset());
    restoreWidgetScrollPositions(scrollPositions);
  }

  function publishWidgets() {
    if (!widgetChannel) return;
    WIDGETS.forEach(([id, label]) => {
      const payload = widgetPayload(id);
      const rendered = renderWidgetBody(id, payload);
      widgetChannel.postMessage({
        type: "state",
        instance_id: `${contextId()}:${id}`,
        widget: id,
        label,
        cluster: "QFw-SLURM-Cluster",
        identity: activeIdentity,
        freshness: state.observed_at || "not observed",
        payload,
        markup: rendered.outerHTML,
        presentation: widgetStates[id] || {},
      });
    });
  }

  function connectWidgetChannel() {
    if (widgetChannel) widgetChannel.close();
    widgetChannel = new BroadcastChannel(`qfw-dashboard-widget-v1:${contextId()}`);
    widgetChannel.addEventListener("message", (event) => {
      const message = event.data || {};
      if (message.type === "request") {
        publishWidgets();
      } else if (message.type === "presentation" && message.widget) {
        widgetStates[message.widget] = {
          ...widgetStates[message.widget], ...message.presentation,
        };
        savePresentation();
        renderDashboard();
        publishWidgets();
      } else if (message.type === "control-action" && message.widget) {
        runPopoutControlAction(message).catch((error) => {
          window.alert(error.message);
        });
      }
    });
  }

  async function runPopoutControlAction(message) {
    const values = message.values || {};
    widgetStates[message.widget] = { ...widgetStates[message.widget], ...values };
    savePresentation();
    if (message.widget !== "cluster-access" && activeIdentity !== "root") {
      throw new Error("administrative operations require the root identity");
    }
    if (message.action === "abort") {
      const group = {
        "cluster-control": "cluster",
        "service-control": "services",
        "node-control": "nodes",
      }[message.widget];
      if (group) await abortOperation(group);
      return;
    }
    if (message.action === "open-shell" && message.widget === "cluster-access") {
      await openClusterShell(values.node || "slurmctld", false);
      return;
    }
    if (message.action !== "run") return;
    if (message.widget === "cluster-control") {
      if (!["start", "stop", "restart", "recreate"].includes(values.operation)) return;
      await runOperation("cluster", {
        action: `cluster-${values.operation}`, target: "cluster",
      });
    } else if (message.widget === "service-control") {
      if (!["start", "stop", "restart", "recover"].includes(values.operation)) return;
      if (!["all", "directory", "nwqsim", "iqm", "gateway"].includes(values.target)) return;
      await runOperation("services", {
        action: `service-${values.operation}`, target: values.target,
      });
    } else if (message.widget === "node-control") {
      if (!["drain", "resume"].includes(values.operation)) return;
      await runOperation("nodes", {
        action: `node-${values.operation}`,
        target: values.node || "",
        reason: values.reason || "qfw-dashboard",
      });
    }
  }

  async function refreshState() {
    try {
      state = await request("/api/qfw-dashboard/state");
    } catch (error) {
      state = {
        health: "unavailable",
        observed_at: new Date().toISOString(),
        sources: {}, operations: [], experiments: [], error: error.message,
      };
    }
    [...(state.operations || []), ...(state.experiments || [])].forEach((item) => {
      const id = item.operation_id || item.experiment_id;
      if (!id || notifiedTerminal.has(id)
          || !["aborted", "succeeded", "failed"].includes(item.status)) return;
      notifiedTerminal.add(id);
      if (window.Notification?.permission === "granted") {
        new window.Notification(`QFw ${item.status}`, {
          body: `${item.action || item.example || "operation"} as ${item.identity}`,
        });
      }
    });
    renderDashboard();
    publishWidgets();
  }

  function progressMatches(event) {
    const pane = runtimeApi.elements.progressOutputPane;
    const component = pane.querySelector("[data-qfw-filter=component]")?.value || "";
    const severity = pane.querySelector("[data-qfw-filter=severity]")?.value || "";
    const search = pane.querySelector("[data-qfw-filter=search]")?.value.toLowerCase() || "";
    const contextValue = pane.querySelector("[data-qfw-filter=context]")?.value || "";
    const since = Number(pane.querySelector("[data-qfw-filter=time]")?.value || 0);
    const mode = pane.querySelector("[data-qfw-filter=mode]")?.value || "timeline";
    const timestamp = Date.parse(event.timestamp || 0);
    return (mode !== "logs" || event.kind === "log")
      && (!component || event.component === component)
      && (!contextValue || [event.instance, event.node, event.job_id,
        event.service_id, event.reservation_id,
        event.experiment_id].includes(contextValue))
      && (!severity || event.severity === severity)
      && (!since || timestamp >= Date.now() - since)
      && (!search || JSON.stringify(event).toLowerCase().includes(search));
  }

  function renderProgress() {
    const filtered = progressEvents.filter(progressMatches).slice(-500);
    window.ElectroBoyFrontend.invokeModule("progress", "clearProgressOutput");
    filtered.forEach((event) => {
      const prefix = `${event.timestamp || ""} ${event.identity || ""} `
        + `${event.component || event.kind || "event"} ${event.severity || "info"}`;
      window.ElectroBoyFrontend.invokeModule(
        "progress", "appendProgressOutput",
        `${prefix} ${event.message || ""}\r\n`,
        event.severity === "error" ? "error" : "",
      );
    });
  }

  function installProgressTools() {
    const pane = runtimeApi.elements.progressOutputPane;
    const header = pane.querySelector(".pane-header");
    if (!header || header.querySelector(".qfw-progress-tools")) return;
    const tools = element("div", "qfw-progress-tools");
    const component = element("select");
    component.dataset.qfwFilter = "component";
    ["", "application", "gateway", "directory", "qpmd", "dvm", "simulator", "provider"]
      .forEach((name) => {
        const option = element("option", "", name || "all components");
        option.value = name;
        component.append(option);
      });
    const source = element("select");
    source.dataset.qfwFilter = "source";
    ["application", "slurm", "gateway", "directory", "nwqsim-qpm",
      "nwqsim-dvm", "nwqsim-simulator", "iqm-qpm", "iqm-provider"]
      .forEach((name) => {
        const option = element("option", "", name);
        option.value = name;
        source.append(option);
      });
    const severity = element("select");
    severity.dataset.qfwFilter = "severity";
    ["", "debug", "info", "warning", "error", "critical"].forEach((name) => {
      const option = element("option", "", name || "all severities");
      option.value = name;
      severity.append(option);
    });
    const search = element("input");
    search.placeholder = "filter logs";
    search.dataset.qfwFilter = "search";
    const contextFilter = element("select");
    contextFilter.dataset.qfwFilter = "context";
    contextFilter.append(element("option", "", "all instances"));
    const time = element("select");
    time.dataset.qfwFilter = "time";
    [[0, "all time"], [300000, "5 minutes"], [3600000, "1 hour"]]
      .forEach(([value, label]) => {
        const option = element("option", "", label);
        option.value = String(value);
        time.append(option);
      });
    const pause = element("button", "", "Pause");
    pause.type = "button";
    pause.addEventListener("click", () => {
      progressPaused = !progressPaused;
      pause.textContent = progressPaused ? "Resume" : "Pause";
    });
    const mode = element("select");
    mode.dataset.qfwFilter = "mode";
    ["Timeline", "Logs"].forEach((label) => {
      const option = element("option", "", label);
      option.value = label.toLowerCase();
      mode.append(option);
    });
    [mode, source, component, contextFilter, severity, time, search].forEach((control) => {
      control.addEventListener("input", renderProgress);
      tools.append(control);
    });
    component.addEventListener("change", () => {
      const values = new Set();
      progressEvents.filter((item) => !component.value
        || item.component === component.value).forEach((item) => {
        [item.instance, item.node, item.job_id, item.service_id,
          item.reservation_id, item.experiment_id]
          .filter(Boolean).forEach((value) => values.add(value));
      });
      contextFilter.replaceChildren(element("option", "", "all instances"));
      [...values].sort().forEach((value) => {
        const option = element("option", "", value);
        option.value = value;
        contextFilter.append(option);
      });
    });
    tools.append(pause, element("span", "qfw-progress-context",
      `${progressIdentity} · connected · live`));
    header.insertBefore(tools, header.querySelector(".pane-actions"));
  }

  async function refreshEvents() {
    if (progressPaused) return;
    try {
      const payload = await request(
        `/api/qfw-dashboard/events?cursor=${eventCursor}&limit=500`
          + `&identity=${encodeURIComponent(progressIdentity)}`,
      );
      if (payload.gap) {
        progressEvents.push({ severity: "warning", message: "log cursor gap" });
      }
      eventCursor = Number(payload.cursor || eventCursor);
      progressEvents = [...progressEvents, ...(payload.events || [])].slice(-2000);
      const pane = runtimeApi.elements.progressOutputPane;
      const mode = pane.querySelector("[data-qfw-filter=mode]")?.value;
      const source = pane.querySelector("[data-qfw-filter=source]")?.value;
      const instance = pane.querySelector("[data-qfw-filter=context]")?.value || "";
      if (mode === "logs" && source && (source !== "application" || instance)) {
        const key = `${source}:${instance}`;
        const logs = await request(
          `/api/qfw-dashboard/logs?source=${encodeURIComponent(source)}`
            + `&cursor=${Number(logCursors[key] || 0)}&limit=500`
            + `&identity=${encodeURIComponent(progressIdentity)}`
            + `&instance=${encodeURIComponent(instance)}`,
        );
        if (logs.gap) {
          progressEvents.push({
            kind: "gap", component: source, severity: "warning",
            message: "log source rotated or exceeded the bounded read window",
          });
        }
        logCursors[key] = Number(logs.cursor || logCursors[key] || 0);
        progressEvents = [...progressEvents, ...(logs.events || [])].slice(-2000);
      }
      renderProgress();
    } catch (error) {
      // Other dashboard state remains usable when logs are unavailable.
    }
  }

  async function pollState() {
    await refreshState();
    if (runtimeApi) polling = window.setTimeout(pollState, 2500);
  }

  async function pollEvents() {
    await refreshEvents();
    if (runtimeApi) eventPolling = window.setTimeout(pollEvents, 1200);
  }

  function activate(runtime) {
    runtimeApi = runtime;
    loadPresentation();
    progressIdentity = activeIdentity;
    dashboardRoot = element("div", "qfw-dashboard");
    originalStatusOutput = runtime.elements.projectStatusOutput;
    originalStatusOutput.replaceWith(dashboardRoot);
    connectWidgetChannel();
    installProgressTools();
    renderDashboard();
    pollState();
    pollEvents();
  }

  function deactivate() {
    window.clearTimeout(polling);
    window.clearTimeout(eventPolling);
    polling = null;
    eventPolling = null;
    widgetChannel?.close();
    widgetChannel = null;
    popupWindows.clear();
    if (dashboardRoot?.isConnected && originalStatusOutput) {
      dashboardRoot.replaceWith(originalStatusOutput);
    }
    dashboardRoot = null;
    originalStatusOutput = null;
    runtimeApi = null;
  }

  window.ElectroBoyFrontend.registerWorkflow({
    id: WORKFLOW_ID,
    mode: WORKFLOW_ID,
    label: "QFw Slurm Cluster",
    order: 20,
    backendPackage: "qfw_slurm_dashboard",
    navigation: "custom",
    paneKinds: [
      { kind: "status", label: "Dashboard" },
      { kind: "progress", label: "Progress" },
      { kind: "artifact", label: "File" },
      { kind: "shell", label: "Shell" },
    ],
    defaultPaneLayout: { type: "leaf", kind: "status" },
    layoutClass: "qfw-slurm-cluster-workflow",
    help: {
      summary: "Operate and observe the QFw virtual Slurm cluster.",
      features: [
        "Inspect cluster, Slurm, QPM, allocation, and experiment state.",
        "Run controlled lifecycle actions under an explicit cluster identity.",
        "Keep cluster recipes, progress, and shells beside the dashboard.",
      ],
    },
    renderNavigation(container) {
      container.replaceChildren(element("p", "", "Cluster dashboard"));
    },
    renderProjectStatus() { return true; },
    activate,
    deactivate,
    actions: { refresh: () => refreshState() },
  });
})();
