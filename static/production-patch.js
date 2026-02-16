/* =========================================
   ARTIFACT ZERO – PRODUCTION STABILITY PATCH
   Non-destructive overlay fixes
   ========================================= */

(function () {

  /* =========================
     1. PREVENT PAGE SNAP
     ========================= */
  const originalScrollBehavior = document.documentElement.style.scrollBehavior;
  document.documentElement.style.scrollBehavior = "auto";

  function preserveScroll(callback) {
    const y = window.scrollY;
    callback();
    requestAnimationFrame(() => {
      window.scrollTo(0, y);
    });
  }

  /* =========================
     2. MENU OVERLAP FIX
     ========================= */
  const menuPanel = document.getElementById("menuPanel");
  const menuOverlay = document.getElementById("menuOverlay");

  if (menuPanel) {
    menuPanel.style.position = "fixed";
    menuPanel.style.top = "0";
    menuPanel.style.right = "0";
    menuPanel.style.height = "100vh";
    menuPanel.style.zIndex = "9999";
  }

  if (menuOverlay) {
    menuOverlay.style.position = "fixed";
    menuOverlay.style.inset = "0";
    menuOverlay.style.zIndex = "9998";
  }

  /* =========================
     3. EXPLORE BUTTON FIX
     ========================= */
  document.querySelectorAll("[data-explore]").forEach(btn => {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      const target = document.getElementById("ai-spend-analyst") ||
                     document.getElementById("interactive-demos") ||
                     document.getElementById("person-gate");
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  /* =========================
     4. INTERACTIVE DEMOS STABILITY
     ========================= */
  const interactiveSection = document.getElementById("interactive-demos");
  if (interactiveSection) {
    interactiveSection.style.minHeight = "400px";
    interactiveSection.style.position = "relative";
  }

  /* =========================
     5. PREVENT LAYOUT SHIFT AFTER ANALYZE
     ========================= */
  const analysisSection = document.querySelector("[data-analysis]");
  if (analysisSection) {
    analysisSection.style.minHeight = analysisSection.offsetHeight + "px";
  }

  /* =========================
     6. BUTTON STABILITY
     ========================= */
  document.querySelectorAll("button").forEach(btn => {
    btn.style.transform = "translateZ(0)";
  });

  console.log("Production stability patch loaded.");
})();
