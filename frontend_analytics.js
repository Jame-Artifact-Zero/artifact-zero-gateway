// frontend_analytics.js
// Frontend Analytics Layer - Integrates with existing trace/observability system
// Sends all events through existing /events endpoint

class FrontendAnalytics {
    constructor() {
        this.sessionId = this.generateSessionId();
        this.userId = this.getUserId();
        this.startTime = Date.now();
        this.eventQueue = [];
        
        // Session state
        this.session = {
            persona: null,
            analysisCount: 0,
            conversionPath: [],
            interactions: {
                clicks: 0,
                keystrokes: 0,
                scrollDepth: 0,
                timeOnPage: 0
            }
        };
        
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.trackPageLoad();
        
        // Flush events every 10 seconds
        setInterval(() => this.flushEvents(), 10000);
        
        // Final flush on page unload
        window.addEventListener('beforeunload', () => this.flushEvents(true));
    }

    generateSessionId() {
        return 'frontend_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    getUserId() {
        let userId = localStorage.getItem('artifact_user_id');
        if (!userId) {
            userId = 'user_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            localStorage.setItem('artifact_user_id', userId);
        }
        return userId;
    }

    setupEventListeners() {
        // Click tracking with business logic
        document.addEventListener('click', (e) => {
            this.handleClick(e);
        }, true);

        // Input tracking for text analysis areas
        document.addEventListener('input', (e) => {
            if (e.target.matches('#in-prompt, #in-answer, textarea')) {
                this.handleInput(e);
            }
        });

        // Scroll depth tracking
        let scrollTimeout;
        window.addEventListener('scroll', () => {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                this.updateScrollDepth();
            }, 200);
        });

        // Form focus tracking
        document.addEventListener('focus', (e) => {
            if (e.target.matches('input, textarea, select')) {
                this.trackEvent('form_focus', {
                    elementId: e.target.id,
                    elementType: e.target.type || e.target.tagName.toLowerCase()
                });
            }
        }, true);
    }

    handleClick(event) {
        this.session.interactions.clicks++;
        
        const element = event.target;
        const clickData = {
            tagName: element.tagName,
            id: element.id,
            className: element.className,
            text: element.textContent?.substring(0, 50) || '',
            position: {
                x: event.clientX,
                y: event.clientY
            }
        };

        // Business logic tracking
        if (element.closest('.persona-card')) {
            const persona = element.closest('.persona-card').dataset.persona;
            this.trackPersonaSelection(persona, clickData);
        } else if (element.matches('.btn-go, #btn-run')) {
            this.trackAnalysisStart(clickData);
        } else if (element.closest('.action-btn')) {
            const amount = this.extractAmount(element.textContent);
            this.trackInvestmentClick(amount, clickData);
        } else if (element.closest('.control-btn')) {
            this.trackRestructureControl(element.dataset.axis, element.dataset.value, clickData);
        } else if (element.closest('.menu-toggle, .menu-panel')) {
            this.trackMenuInteraction(element.className, clickData);
        } else {
            // Generic click
            this.trackEvent('click', clickData);
        }
    }

    handleInput(event) {
        const target = event.target;
        this.session.interactions.keystrokes++;
        
        this.trackEvent('text_input', {
            elementId: target.id,
            valueLength: target.value.length,
            isPaste: event.inputType === 'insertFromPaste',
            inputType: event.inputType
        });
    }

    trackPersonaSelection(persona, clickData) {
        this.session.persona = persona;
        this.session.conversionPath.push({
            step: 'persona_selection',
            persona: persona,
            timestamp: Date.now()
        });

        this.trackEvent('persona_selection', {
            persona: persona,
            sessionStep: this.session.conversionPath.length,
            ...clickData
        });
    }

    trackAnalysisStart(clickData) {
        this.session.analysisCount++;
        this.session.conversionPath.push({
            step: 'analysis_start',
            count: this.session.analysisCount,
            timestamp: Date.now()
        });

        this.trackEvent('nti_analysis_start', {
            analysisNumber: this.session.analysisCount,
            persona: this.session.persona,
            sessionStep: this.session.conversionPath.length,
            ...clickData
        });
    }

    trackInvestmentClick(amount, clickData) {
        this.session.conversionPath.push({
            step: 'investment_click',
            amount: amount,
            timestamp: Date.now()
        });

        this.trackEvent('investment_click', {
            amount: amount,
            persona: this.session.persona,
            analysisCount: this.session.analysisCount,
            conversionPath: this.session.conversionPath,
            ...clickData
        });
    }

    trackRestructureControl(axis, value, clickData) {
        this.trackEvent('restructure_control', {
            axis: axis,
            value: value,
            persona: this.session.persona,
            ...clickData
        });
    }

    trackMenuInteraction(elementClass, clickData) {
        this.trackEvent('menu_interaction', {
            action: elementClass.includes('toggle') ? 'open' : 'navigate',
            ...clickData
        });
    }

    trackNTIResult(result) {
        this.session.conversionPath.push({
            step: 'analysis_complete',
            integrityScore: result.nii?.nii_score,
            timestamp: Date.now()
        });

        this.trackEvent('nti_analysis_complete', {
            integrityScore: result.nii?.nii_score,
            failureModes: result.interaction_matrix?.dominance_detected,
            tiltCount: result.tilt_taxonomy?.length || 0,
            latency: result.telemetry?.latency_ms,
            persona: this.session.persona,
            analysisNumber: this.session.analysisCount,
            sessionStep: this.session.conversionPath.length
        });
    }

    trackConversionComplete(type, data = {}) {
        this.session.conversionPath.push({
            step: 'conversion_complete',
            type: type,
            timestamp: Date.now()
        });

        this.trackEvent('conversion_complete', {
            conversionType: type,
            persona: this.session.persona,
            analysisCount: this.session.analysisCount,
            sessionDuration: Date.now() - this.startTime,
            fullConversionPath: this.session.conversionPath,
            ...data
        });
    }

    updateScrollDepth() {
        const scrollTop = window.pageYOffset;
        const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
        const scrollPercent = Math.round((scrollTop / scrollHeight) * 100) || 0;
        
        if (scrollPercent > this.session.interactions.scrollDepth) {
            this.session.interactions.scrollDepth = scrollPercent;
            
            // Only track significant scroll milestones
            if ([25, 50, 75, 90, 100].includes(scrollPercent)) {
                this.trackEvent('scroll_milestone', {
                    scrollPercent: scrollPercent,
                    persona: this.session.persona
                });
            }
        }
    }

    trackPageLoad() {
        this.trackEvent('page_load', {
            url: window.location.href,
            referrer: document.referrer,
            userAgent: navigator.userAgent,
            viewport: {
                width: window.innerWidth,
                height: window.innerHeight
            },
            loadTime: performance.now()
        });
    }

    trackEvent(eventName, eventData = {}) {
        const event = {
            event: eventName,
            data: {
                sessionId: this.sessionId,
                userId: this.userId,
                timestamp: Date.now(),
                timeOnPage: Date.now() - this.startTime,
                sessionInteractions: this.session.interactions,
                ...eventData
            }
        };

        this.eventQueue.push(event);
    }

    async flushEvents(immediate = false) {
        if (this.eventQueue.length === 0) return;

        const events = [...this.eventQueue];
        this.eventQueue = [];

        try {
            if (immediate && navigator.sendBeacon) {
                // Use sendBeacon for page unload
                for (const event of events) {
                    navigator.sendBeacon('/events', JSON.stringify(event));
                }
            } else {
                // Send batch to existing /events endpoint
                for (const event of events) {
                    await fetch('/events', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(event)
                    });
                }
            }
        } catch (error) {
            // Re-queue events on failure
            this.eventQueue.unshift(...events);
            console.error('Analytics flush failed:', error);
        }
    }

    extractAmount(text) {
        const match = text.match(/\$?([\d,]+)K?\+?/);
        return match ? match[1] + (text.includes('K') ? 'K' : '') : null;
    }

    // Public API for integration
    getSessionData() {
        return {
            sessionId: this.sessionId,
            userId: this.userId,
            persona: this.session.persona,
            analysisCount: this.session.analysisCount,
            conversionPath: this.session.conversionPath,
            interactions: this.session.interactions,
            timeOnPage: Date.now() - this.startTime
        };
    }
}

// Auto-initialize and expose globally
if (typeof window !== 'undefined') {
    window.frontendAnalytics = new FrontendAnalytics();
    
    // Expose key tracking functions globally
    window.trackNTIResult = (result) => window.frontendAnalytics.trackNTIResult(result);
    window.trackConversionComplete = (type, data) => window.frontendAnalytics.trackConversionComplete(type, data);
    
    console.log('Frontend Analytics initialized - feeding into existing /events endpoint');
}
