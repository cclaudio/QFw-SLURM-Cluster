(function () {
  "use strict";

  window.ElectroBoyFrontend.registerWorkflow({
    id: "qfw-slurm-cluster",
    mode: "qfw-slurm-cluster",
    label: "QFw Slurm Cluster",
    order: 20,
    backendPackage: "qfw_slurm_dashboard",
    navigation: "custom",
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
      container.replaceChildren();
      const text = document.createElement("p");
      text.textContent = "Cluster dashboard";
      container.append(text);
    },
    renderProjectStatus() {
      return false;
    },
    activate() {},
    deactivate() {},
    actions: {},
  });
})();
