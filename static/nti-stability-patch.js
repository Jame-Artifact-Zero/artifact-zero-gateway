// Artifact Zero Stability Patch
// Zero backend changes. Zero index replacement.
// Drop-in layout stabilization.

(function () {

    console.log("NTI Stability Patch Loaded");

    // 1. Disable scroll snapping behavior completely
    document.documentElement.style.scrollBehavior = "auto";
    document.body.style.scrollBehavior = "auto";

    // 2. Prevent layout shift when sections reveal
    const resultsContainer = document.getElementById("resultsContainer");
    if (resultsContainer) {
        resultsContainer.style.minHeight = "600px";
    }

    // 3. Override any scrollIntoView calls
    Element.prototype._originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () {
        console.log("Scroll blocked by NTI patch");
        return;
    };

    // 4. Force stable container height calculation
    function stabilizeLayout() {
        const sections = document.querySelectorAll(".result-section");
        sections.forEach(section => {
            section.style.scrollMarginTop = "0px";
        });
    }

    // 5. Run after load
    window.addEventListener("load", function () {
        stabilizeLayout();
    });

})();
