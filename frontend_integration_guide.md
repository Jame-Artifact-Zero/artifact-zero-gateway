# Frontend Analytics Integration

## Overview
This frontend analytics layer integrates with your existing trace/observability system by feeding all events through your existing `/events` endpoint.

## Integration Points

### 1. Uses Your Existing Infrastructure:
- **Sends to `/events`** (your existing endpoint in app.py)
- **Follows your JSON logging pattern**
- **Integrates with TraceLogger** (writes to core_trace.jsonl)
- **Uses your session management** approach

### 2. Business Logic Tracking:
- **Persona selection tracking** → Feeds conversion funnel
- **NTI analysis events** → Tracks analysis-to-conversion pipeline  
- **Investment button clicks** → Captures conversion intent
- **Restructuring interactions** → Measures feature engagement
- **Menu/FAQ usage** → Content discovery patterns

### 3. Technical Implementation:

#### Add to your index.html:
```html
<!-- Add before closing </body> tag -->
<script src="frontend_analytics.js"></script>
<script>
// Integration with existing NTI analysis
function renderResults() {
    // ... your existing code ...
    
    // ADD THIS: Track analysis completion
    if (lastNTI && !lastNTI.error) {
        window.trackNTIResult(lastNTI);
    }
}

// Integration with investment flows
async function investNow(amount) {
    // ... your existing code ...
    
    if (data.url) {
        // ADD THIS: Track conversion start
        window.trackConversionComplete('investment_initiated', { amount });
        window.location.href = data.url;
    }
}
</script>
```

## Data Flow

### Frontend Events → Your Existing System:
```
User Interaction 
  ↓
Frontend Analytics 
  ↓  
/events endpoint (app.py)
  ↓
SQLite events table
  ↓
TraceLogger (core_trace.jsonl)
  ↓
Your observability_layer.py
```

## Event Types Captured

### Conversion Funnel:
- `persona_selection` - Which persona user picked
- `nti_analysis_start` - User clicks analyze button  
- `nti_analysis_complete` - Results rendered with integrity scores
- `investment_click` - User clicks investment button
- `conversion_complete` - Payment flow initiated

### Engagement:
- `restructure_control` - Interactive text transformation usage
- `menu_interaction` - FAQ/help system usage
- `scroll_milestone` - 25%, 50%, 75%, 100% page depth
- `text_input` - Length/patterns in analysis text areas

### Technical:
- `page_load` - Performance and referrer data
- `form_focus` - Field interaction patterns
- `click` - All other clicks with element details

## Analytics Dashboard Ready

Your existing `/events` endpoint now receives rich frontend data. You can query it with:

```sql
-- Conversion funnel analysis
SELECT event_name, COUNT(*) 
FROM events 
WHERE event_name IN ('persona_selection', 'nti_analysis_start', 'investment_click')
GROUP BY event_name;

-- Most popular persona
SELECT JSON_EXTRACT(event_json, '$.persona'), COUNT(*)
FROM events 
WHERE event_name = 'persona_selection'
GROUP BY JSON_EXTRACT(event_json, '$.persona');

-- Investment intent by persona
SELECT 
    JSON_EXTRACT(event_json, '$.persona') as persona,
    JSON_EXTRACT(event_json, '$.amount') as amount,
    COUNT(*) as clicks
FROM events 
WHERE event_name = 'investment_click'
GROUP BY persona, amount;
```

## Zero Code Changes Required

This integrates with your existing system without any changes to:
- ✅ app.py (uses existing /events endpoint)  
- ✅ trace.py (continues writing to core_trace.jsonl)
- ✅ observability_layer.py (continues risk scoring)
- ✅ Database schema (uses existing events table)

Just add the JavaScript file and 2 lines of integration code.

## Deployment

1. **Add frontend_analytics.js** to your gateway folder
2. **Include in index.html** (one script tag)  
3. **Add 2 integration lines** to existing functions
4. **Frontend analytics** start flowing to your existing trace system

Your robust backend tracking + comprehensive frontend tracking = complete user journey visibility.
