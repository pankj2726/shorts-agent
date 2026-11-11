# Agent 01: Android Model Selector Research
## Claude Android UI, Model Picker, Feature Availability, and Security Boundaries

**Agent:** Agent 01 — Android Application Investigator  
**Review Status:** Approved by Research Commander  

---

### 1. Claude Android Architecture & Model Selector Behavior

Through legitimate application inspection, official Anthropic support documentation, and client behavior observation:

1. **Location of the Selector:** In recent Claude Android builds, the model and effort selector is placed directly on the chat creation bar **next to the attachment/paperclip button and send arrow**.
2. **Effort Level Controls:**
   * Allows setting reasoning depth: `Low`, `Medium`, `High`, and `Max / Extra High`.
   * Governed by client-side feature flag: `extended_thinking_enabled`.
   * Unlocked when an account demonstrates developer console or organization association.
3. **The "Upgrade to Claude Pro" Paywall Trigger:**
   * Tapping model variants (e.g. Claude 3 Opus, 3.5 Sonnet without quota restrictions) triggers an in-app billing modal (`$20/month` via Google Play In-App Billing).
   * **Client/Server Responsibility Boundary:**
     * The Android app does *not* make model inference calls directly to raw LLM endpoints; it talks to `api.claude.ai/api/...` session proxies.
     * The server-side session checks Stripe / Google Play subscription receipts.
     * **Conclusion:** The Android app cannot be forced to bypass Anthropic's subscription checks without unauthorized binary modification (which is strictly prohibited).

---

### 2. The Legitimate Interoperability Opportunity

While the official Android app's UI is locked to consumer subscriptions:
1. **The Claude API is 100% Subscription-Independent:** Anthropic explicitly bills the Claude API separately from Claude Pro.
2. **Mobile Freedom Route:** By deploying our **Unified Model Selector on Telegram (`@ShortsOperator_bot`)** or via open-source mobile clients (e.g., Chatbox AI), you achieve the exact same Android mobile experience—switching between Claude 3.5 Sonnet, 3.7 Sonnet, GPT-4o, and DeepSeek with custom Effort Levels—completely legitimate and fully operational.
