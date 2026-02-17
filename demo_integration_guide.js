// Integration code for adding Interactive Demos to existing index.html

// 1. ADD TO HTML (after existing demo-section):
const INTERACTIVE_DEMO_HTML = `
<div class="interactive-demo-section" id="interactiveDemoSection" style="display:none;">
    <div class="demo-selector">
        <h3>Experience NTI in Action</h3>
        <p>See exactly what NTI fixes in real-time with interactive scenarios</p>
        <div class="demo-buttons">
            <button class="demo-select-btn" onclick="startDemo('email_response')">
                📧 Fix Email Response
            </button>
            <button class="demo-select-btn" onclick="startDemo('customer_support')">
                🚨 Handle Customer Crisis  
            </button>
            <button class="demo-select-btn" onclick="startDemo('project_update')">
                📊 Project Status Update
            </button>
        </div>
    </div>
    <div id="interactiveDemoContainer"></div>
</div>
`;

// 2. ADD TO CSS (additional styles for demo selector):
const DEMO_SELECTOR_CSS = `
.interactive-demo-section {
    margin-top: 32px;
    padding: 24px;
    background: var(--bg2);
    border: 1px solid var(--bg3);
    border-radius: var(--r2);
}

.demo-selector {
    text-align: center;
    margin-bottom: 24px;
}

.demo-selector h3 {
    font-family: var(--mono);
    font-size: 18px;
    color: var(--green);
    margin-bottom: 8px;
}

.demo-selector p {
    font-size: 14px;
    color: var(--fg2);
    margin-bottom: 20px;
}

.demo-buttons {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    max-width: 600px;
    margin: 0 auto;
}

.demo-select-btn {
    padding: 12px 16px;
    background: var(--bg3);
    border: 1px solid var(--bg4);
    color: var(--fg);
    border-radius: var(--r);
    font-family: var(--sans);
    font-size: 14px;
    cursor: pointer;
    transition: all .2s;
}

.demo-select-btn:hover {
    border-color: var(--green);
    background: var(--bg4);
    transform: translateY(-1px);
}

@media (max-width: 768px) {
    .demo-buttons {
        grid-template-columns: 1fr;
    }
}
`;

// 3. INTEGRATION FUNCTIONS TO ADD TO EXISTING JAVASCRIPT:

function showInteractiveDemos() {
    document.getElementById('interactiveDemoSection').style.display = 'block';
    document.getElementById('interactiveDemoSection').scrollIntoView({ 
        behavior: 'smooth', 
        block: 'start' 
    });
    
    // Track demo section shown
    if (window.frontendAnalytics) {
        window.frontendAnalytics.trackEvent('interactive_demos_shown', {
            persona: currentPersona
        });
    }
}

function startDemo(demoType) {
    window.interactiveDemos.initializeDemo(demoType, 'interactiveDemoContainer');
    
    // Hide selector, show demo
    document.querySelector('.demo-selector').style.display = 'none';
    
    // Track demo selection
    if (window.frontendAnalytics) {
        window.frontendAnalytics.trackEvent('demo_selected', {
            demoType: demoType,
            persona: currentPersona
        });
    }
}

// 4. MODIFY EXISTING renderResults() FUNCTION:
// Add this after rendering persona-specific results:

function renderResults() {
    // ... existing renderResults code ...
    
    // ADD THIS AT THE END:
    // Show interactive demos after analysis
    setTimeout(() => {
        showInteractiveDemos();
    }, 1000);
}

// 5. ADD TO EXISTING PERSONAS (modify PERSONAS object):
const ENHANCED_PERSONAS = {
    explorer: {
        name: 'Explorer',
        title: 'See AI instability in real time',
        desc: 'Paste any AI prompt and response below. Artifact Zero will show you exactly where the AI drifted, deflected, or collapsed your constraints — without using any AI itself.',
        demoPrompt: 'Try our interactive demos to see NTI fix real problems in real-time.'
    },
    executive: {
        name: 'Decision-Maker', 
        title: 'AI risk & cost at a glance',
        desc: 'Every AI interaction carries hidden risk: constraint collapse, objective drift, unnecessary token spend. Paste a real exchange to see the exposure.',
        demoPrompt: 'Experience how NTI prevents costly communication failures.'
    },
    // ... other personas with demoPrompt added
};

// 6. INTEGRATION CHECKLIST:

/*
TO INTEGRATE INTO EXISTING INDEX.HTML:

1. Add CSS files to <head>:
   <link rel="stylesheet" href="interactive_demos.css">

2. Add JavaScript files before closing </body>:
   <script src="interactive_demos.js"></script>

3. Add HTML section after existing demo-area:
   [Insert INTERACTIVE_DEMO_HTML]

4. Add CSS to existing <style> section:
   [Insert DEMO_SELECTOR_CSS]

5. Add JavaScript functions to existing <script>:
   [Insert showInteractiveDemos(), startDemo(), modify renderResults()]

6. Files to add to gateway folder:
   - interactive_demos.js
   - interactive_demos.css

RESULT: After any NTI analysis, users see interactive demos that:
- Guide them into communication problems
- Show their actual response with problems highlighted  
- Generate NTI-enhanced version with improvements
- End with "API mic drop" moment
*/
