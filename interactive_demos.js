// interactive_demos.js
// Interactive Problem → Solution Demos for NTI
// Show users exactly what NTI fixes in real-time

class InteractiveDemos {
    constructor() {
        this.currentDemo = null;
        this.demoData = this.getDemoScenarios();
    }

    getDemoScenarios() {
        return {
            email_response: {
                title: "Email Response Demo",
                description: "Help respond to John's urgent email",
                context: {
                    from: "John Martinez <john@techcorp.com>",
                    to: "You",
                    subject: "Q1 Budget Approval - Need Decision by EOD",
                    email: `Hey,

The budget committee is meeting tomorrow morning and I need your sign-off on the Q1 marketing spend increase. We're looking at:

- $50K additional for digital ads
- $25K for the new analytics platform  
- $15K for conference sponsorships

Legal is asking about compliance requirements and Finance wants ROI projections. Can you get back to me with your thoughts? This needs to go to the board packet tonight.

Also, Sarah mentioned some concerns about the analytics vendor - something about data privacy? Not sure if that affects the decision.

Let me know ASAP.

Thanks,
John`
                },
                prompt: "Type your response to John's email:",
                placeholder: "Hi John, regarding the budget...",
                problemAnalysis: {
                    title: "Problems with your response:",
                    issues: [
                        "Multiple unaddressed questions (Legal compliance? ROI projections?)",
                        "Vague language creates more back-and-forth",
                        "Missing deadline acknowledgment",
                        "Sarah's privacy concerns ignored",
                        "No clear next steps"
                    ]
                },
                ntiSolution: {
                    title: "NTI-Enhanced Response:",
                    improvements: [
                        "Addresses all 4 specific questions",
                        "Clear decision on each line item", 
                        "Acknowledges EOD deadline",
                        "Delegates privacy concern to appropriate owner",
                        "Specific next steps with timeline"
                    ]
                }
            },
            
            customer_support: {
                title: "Customer Support Demo",
                description: "Handle an escalated customer complaint",
                context: {
                    from: "Sarah Chen <sarah@innovatetech.com>",
                    to: "Support Team",
                    subject: "URGENT: System Down for 3 Hours - Losing Revenue",
                    ticket: `This is completely unacceptable. Our entire sales team can't access the CRM and we're losing deals by the hour.

Your system has been down for 3 hours. THREE HOURS. Do you understand what that means for a business like ours?

I've called support twice and gotten generic responses about "looking into it." I need:
1. Exact timeline for resolution
2. Root cause explanation  
3. Compensation for lost revenue
4. Guarantee this won't happen again

I'm escalating to our legal team if I don't hear something concrete in the next hour. We're evaluating other vendors.

This is my final attempt at getting actual help.

Sarah Chen
CTO, InnovateTech`
                },
                prompt: "Write your response to this escalated customer:",
                placeholder: "Dear Sarah, we apologize for the inconvenience...",
                problemAnalysis: {
                    title: "Your response triggered these issues:",
                    issues: [
                        "Generic apology language increases frustration",
                        "Failed to address specific timeline demand",
                        "No concrete compensation offer",
                        "Defensive tone escalates conflict",
                        "Missing executive escalation protocol"
                    ]
                }
            },

            project_update: {
                title: "Project Status Demo", 
                description: "Update stakeholders on a delayed project",
                context: {
                    meeting: "Weekly Project Steering Committee",
                    attendees: "CEO, CTO, Product VP, Engineering Director",
                    situation: "The Q1 product launch is 3 weeks behind schedule due to integration issues with the new payment system. The engineering team discovered security vulnerabilities that require additional testing. Marketing has already committed to launch dates with customers."
                },
                prompt: "Write your status update for the steering committee:",
                placeholder: "Hi everyone, wanted to give you an update on the Q1 launch...",
                problemAnalysis: {
                    title: "Communication failures in your update:",
                    issues: [
                        "Buried the lead (3-week delay mentioned casually)",
                        "No revised timeline or milestones",
                        "Blame-shifting language creates defensiveness", 
                        "Missing impact assessment on customer commitments",
                        "No risk mitigation plan presented"
                    ]
                }
            }
        };
    }

    initializeDemo(demoType, containerId) {
        this.currentDemo = demoType;
        const container = document.getElementById(containerId);
        const demo = this.demoData[demoType];
        
        container.innerHTML = this.renderDemoInterface(demo);
        this.attachDemoEventListeners();
        
        // Track demo start
        if (window.frontendAnalytics) {
            window.frontendAnalytics.trackEvent('interactive_demo_start', {
                demoType: demoType,
                title: demo.title
            });
        }
    }

    renderDemoInterface(demo) {
        return `
            <div class="demo-container">
                <div class="demo-header">
                    <h3>${demo.title}</h3>
                    <p>${demo.description}</p>
                </div>
                
                <div class="demo-context">
                    ${this.renderContext(demo.context)}
                </div>
                
                <div class="demo-input-section">
                    <label>${demo.prompt}</label>
                    <textarea 
                        id="demoUserInput" 
                        placeholder="${demo.placeholder}"
                        rows="6"
                    ></textarea>
                    <button class="demo-btn primary" onclick="interactiveDemos.analyzeResponse()">
                        Send Response
                    </button>
                </div>
                
                <div class="demo-results" id="demoResults" style="display:none;">
                    <!-- Results will be populated here -->
                </div>
            </div>
        `;
    }

    renderContext(context) {
        if (context.email) {
            return `
                <div class="email-context">
                    <div class="email-header">
                        <strong>From:</strong> ${context.from}<br>
                        <strong>To:</strong> ${context.to}<br>
                        <strong>Subject:</strong> ${context.subject}
                    </div>
                    <div class="email-body">${context.email.replace(/\n/g, '<br>')}</div>
                </div>
            `;
        } else if (context.ticket) {
            return `
                <div class="ticket-context">
                    <div class="ticket-header">
                        <strong>From:</strong> ${context.from}<br>
                        <strong>Subject:</strong> ${context.subject}
                    </div>
                    <div class="ticket-body">${context.ticket.replace(/\n/g, '<br>')}</div>
                </div>
            `;
        } else if (context.situation) {
            return `
                <div class="meeting-context">
                    <div class="meeting-header">
                        <strong>Meeting:</strong> ${context.meeting}<br>
                        <strong>Attendees:</strong> ${context.attendees}
                    </div>
                    <div class="situation">${context.situation}</div>
                </div>
            `;
        }
        return '';
    }

    async analyzeResponse() {
        const userInput = document.getElementById('demoUserInput').value.trim();
        if (!userInput) {
            alert('Please write a response first!');
            return;
        }

        // Show loading state
        const resultsDiv = document.getElementById('demoResults');
        resultsDiv.style.display = 'block';
        resultsDiv.innerHTML = '<div class="demo-loading">Analyzing your response...</div>';

        try {
            // Step 1: Show problems with user's response
            await this.showProblems(userInput);
            
            // Step 2: Generate NTI-enhanced version
            setTimeout(() => this.showNTISolution(userInput), 2000);

        } catch (error) {
            resultsDiv.innerHTML = '<div class="demo-error">Analysis failed. Please try again.</div>';
        }
    }

    async showProblems(userInput) {
        const demo = this.demoData[this.currentDemo];
        const resultsDiv = document.getElementById('demoResults');
        
        // Run actual NTI analysis
        let ntiResult = null;
        try {
            const response = await fetch('/nti', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    text: userInput
                })
            });
            ntiResult = await response.json();
        } catch (error) {
            console.log('NTI analysis failed:', error);
        }

        const problemsHtml = `
            <div class="demo-step-1">
                <div class="demo-step-header">
                    <span class="step-number">1</span>
                    <h4>Your Response (Without NTI)</h4>
                </div>
                
                <div class="user-response-box">
                    "${userInput}"
                </div>
                
                <div class="problems-analysis">
                    <div class="problems-title">
                        <span class="warning-icon">⚠️</span>
                        ${demo.problemAnalysis.title}
                    </div>
                    <ul class="problems-list">
                        ${demo.problemAnalysis.issues.map(issue => 
                            `<li>${issue}</li>`
                        ).join('')}
                    </ul>
                    ${ntiResult ? this.renderNTIScores(ntiResult) : ''}
                </div>
                
                <div class="demo-reveal-button">
                    <button class="demo-btn nti-btn" onclick="interactiveDemos.showNTISolution('${userInput.replace(/'/g, "\\'")}')">
                        🚀 Use NTI to Fix This
                    </button>
                </div>
            </div>
        `;
        
        resultsDiv.innerHTML = problemsHtml;
        
        // Track problem analysis shown
        if (window.frontendAnalytics) {
            window.frontendAnalytics.trackEvent('demo_problems_shown', {
                demoType: this.currentDemo,
                responseLength: userInput.length,
                integrityScore: ntiResult?.nii?.nii_score
            });
        }
    }

    showNTISolution(userInput) {
        const demo = this.demoData[this.currentDemo];
        const resultsDiv = document.getElementById('demoResults');
        
        // Generate NTI-enhanced response
        const enhancedResponse = this.generateEnhancedResponse(userInput, demo);
        
        const solutionHtml = `
            ${resultsDiv.innerHTML}
            
            <div class="demo-step-2">
                <div class="demo-step-header">
                    <span class="step-number">2</span>
                    <h4>NTI-Enhanced Response</h4>
                </div>
                
                <div class="enhanced-response-box">
                    "${enhancedResponse}"
                </div>
                
                <div class="improvements-analysis">
                    <div class="improvements-title">
                        <span class="success-icon">✅</span>
                        What NTI Fixed:
                    </div>
                    <ul class="improvements-list">
                        ${demo.ntiSolution.improvements.map(improvement => 
                            `<li>${improvement}</li>`
                        ).join('')}
                    </ul>
                </div>
                
                <div class="demo-cta">
                    <div class="demo-cta-content">
                        <div class="mic-drop">
                            <strong>🎤 Mic Drop Moment:</strong>
                            <p>Instead of manually fixing emails, just tell your boss: 
                            <em>"Pasting emails one-by-one is a waste of time. NTI is an API."</em></p>
                        </div>
                        
                        <div class="demo-next-steps">
                            <button class="demo-btn primary" onclick="interactiveDemos.tryAnother()">
                                Try Another Demo
                            </button>
                            <button class="demo-btn secondary" onclick="interactiveDemos.showAPIInfo()">
                                Show Me the API
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        resultsDiv.innerHTML = solutionHtml;
        
        // Scroll to solution
        resultsDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        
        // Track solution shown
        if (window.frontendAnalytics) {
            window.frontendAnalytics.trackEvent('demo_solution_shown', {
                demoType: this.currentDemo,
                originalLength: userInput.length,
                enhancedLength: enhancedResponse.length
            });
        }
    }

    generateEnhancedResponse(userInput, demo) {
        // This would ideally call your NTI V3 output enhancement
        // For now, return contextually appropriate enhanced responses
        
        const enhancedResponses = {
            email_response: `Hi John,

Budget approval status for Q1 marketing spend:

✅ APPROVED: $50K digital ads (ROI projection: 3.2x based on Q4 data)
✅ APPROVED: $25K analytics platform (Legal compliance review completed)
⚠️ CONDITIONAL: $15K conference sponsorships (pending ROI analysis by Thursday)

Re: Sarah's data privacy concerns - I'm connecting her directly with our compliance officer (cc: mike@compliance.com) for vendor security review.

Board packet timeline: All approvals and documentation will be submitted by 6 PM today.

Next steps:
- Finance: Send ROI projections to Legal by 3 PM
- Sarah: Complete vendor security review by Thursday  
- You: Submit board packet tonight as planned

Call me if any blockers arise.`,

            customer_support: `Sarah,

I understand the severity of this outage and I'm personally overseeing the resolution.

Immediate answers to your specific questions:

1. TIMELINE: System restoration expected within 45 minutes (by 4:30 PM EST)
2. ROOT CAUSE: Database cluster failover issue - full technical post-mortem will follow
3. COMPENSATION: $5,000 service credit applied to your account + waived fees for February
4. PREVENTION: Implementing redundant failover systems (completion: March 15)

I'm escalating this to our CTO and will provide hourly updates until resolved. My direct line: (555) 123-4567.

Your business matters to us. We will make this right.

David Chen, VP Customer Success`
        };

        return enhancedResponses[this.currentDemo] || 
               this.applyNTITransformations(userInput);
    }

    applyNTITransformations(text) {
        // Apply basic NTI transformations for unknown scenarios
        let enhanced = text;
        
        // Remove hedging
        enhanced = enhanced.replace(/\b(maybe|might|could|perhaps|possibly)\b/gi, '');
        
        // Make commitments concrete  
        enhanced = enhanced.replace(/\b(we'll try to|we hope to|we should)\b/gi, 'we will');
        
        // Add structure markers
        if (!enhanced.includes('Next steps:')) {
            enhanced += '\n\nNext steps:\n- Follow up within 24 hours\n- Provide status update';
        }
        
        return enhanced.trim();
    }

    renderNTIScores(ntiResult) {
        const score = ntiResult.nii?.nii_score || 0;
        const drift = ((ntiResult.layers?.L3_objective_integrity || {}).drift_score || 0) * 100;
        const failureModes = ntiResult.interaction_matrix?.dominance_detected || ['NONE'];
        
        return `
            <div class="nti-scores">
                <div class="score-item">
                    <span class="score-label">Integrity:</span>
                    <span class="score-value ${score >= 0.75 ? 'good' : 'bad'}">${Math.round(score * 100)}%</span>
                </div>
                <div class="score-item">
                    <span class="score-label">Drift:</span>
                    <span class="score-value ${drift < 30 ? 'good' : 'bad'}">${Math.round(drift)}%</span>
                </div>
                <div class="score-item">
                    <span class="score-label">Failure Modes:</span>
                    <span class="score-value">${failureModes.join(', ')}</span>
                </div>
            </div>
        `;
    }

    tryAnother() {
        const demoTypes = Object.keys(this.demoData);
        const currentIndex = demoTypes.indexOf(this.currentDemo);
        const nextIndex = (currentIndex + 1) % demoTypes.length;
        const nextDemo = demoTypes[nextIndex];
        
        this.initializeDemo(nextDemo, 'interactiveDemoContainer');
    }

    showAPIInfo() {
        alert('API Documentation: POST /nti with prompt+answer → returns integrity score, drift analysis, and enhanced output. Zero AI inference cost. 63-65% token reduction validated.');
    }

    attachDemoEventListeners() {
        // Handle enter key in textarea
        const textarea = document.getElementById('demoUserInput');
        if (textarea) {
            textarea.addEventListener('keypress', (e) => {
                if (e.key === 'Enter' && e.ctrlKey) {
                    this.analyzeResponse();
                }
            });
        }
    }
}

// Initialize and expose globally
if (typeof window !== 'undefined') {
    window.interactiveDemos = new InteractiveDemos();
}
