
(function() {
    let currentLanguage = 'en-IN';
    let currentUtterance = null;
    let isGenerating = false;
    let abortController = null;

    // Inject HTML structure
    const widgetHTML = `
        <div id="chat-widget-window">
            <div class="chat-widget-header">
                <h3>Kalpataru AI</h3>
                <select id="chat-widget-lang" class="chat-widget-lang-select">
                    <option value="en-IN">English</option>
                    <option value="hi-IN">हिन्दी</option>
                    <option value="bn-IN">বাংলা</option>
                </select>
            </div>
            <div id="chat-widget-messages">
                <div class="chat-msg-container bot">
                    <div class="chat-msg-header">
                        <button class="audio-btn" title="Listen"><i class="fa-solid fa-volume-high"></i></button>
                    </div>
                    <div class="chat-msg bot">Hello! I am Kalpataru, your AI farming assistant. How can I help you today? 🌱</div>
                </div>
            </div>
            <div class="chat-widget-input-area">
                <button id="chat-widget-voice" title="Voice Input"><i class="fa-solid fa-microphone"></i></button>
                <input type="text" id="chat-widget-input" placeholder="Type or speak..." autocomplete="off">
                <button id="chat-widget-send"><i class="fa-solid fa-paper-plane"></i></button>
            </div>
        </div>
        <div id="chat-widget-button">
            <i class="fa-solid fa-comment-dots"></i>
        </div>
    `;

    const container = document.createElement('div');
    container.innerHTML = widgetHTML;
    document.body.appendChild(container);

    const btn = document.getElementById('chat-widget-button');
    const chatWindow = document.getElementById('chat-widget-window');
    const input = document.getElementById('chat-widget-input');
    const sendBtn = document.getElementById('chat-widget-send');
    const voiceBtn = document.getElementById('chat-widget-voice');
    const langSelect = document.getElementById('chat-widget-lang');
    const messages = document.getElementById('chat-widget-messages');

    // Reset history on load (requested behavior)
    async function resetChat() {
        try {
            await fetch('/api/chat-clear', { method: 'POST' });
            // UI is already at initial state (welcome message)
            console.log("[Chat] History cleared for fresh start");
        } catch (e) { console.error("History reset error:", e); }
    }
    resetChat();

    // Speech Recognition
    let recognition = null;
    const isSecure = window.location.protocol === 'https:' || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        recognition.continuous = true; // Stay active to allow longer pauses
        recognition.interimResults = true;

        let silenceTimer = null;

        recognition.onstart = () => {
            console.log('[Voice] Recognition started');
            voiceBtn.classList.add('listening');
            input.placeholder = 'Listening...';
        };
        
        recognition.onresult = (event) => {
            let finalTranscript = '';
            let interimTranscript = '';
            for (let i = event.resultIndex; i < event.results.length; ++i) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                } else {
                    interimTranscript += event.results[i][0].transcript;
                }
            }
            if (finalTranscript || interimTranscript) {
                input.value = finalTranscript || interimTranscript;
            }

            // Reset silence timer: Stop recognition after 2.5s of silence
            clearTimeout(silenceTimer);
            silenceTimer = setTimeout(() => {
                if (recognition) recognition.stop();
            }, 2500); 
        };

        recognition.onend = () => {
            console.log('[Voice] Recognition ended');
            voiceBtn.classList.remove('listening');
            input.placeholder = 'Type or speak...';
            clearTimeout(silenceTimer);

            // Auto-send if there is text
            const text = input.value.trim();
            if (text && !isGenerating) {
                sendMessage();
            }
        };
        
        recognition.onerror = (event) => {
            console.error('[Voice] Error:', event.error);
            voiceBtn.classList.remove('listening');
        };
    }

    voiceBtn.addEventListener('click', () => {
        if (!recognition) return alert('Speech recognition not supported.');
        recognition.lang = currentLanguage;
        try {
            recognition.start();
        } catch (e) { 
            recognition.stop(); 
        }
    });

    langSelect.addEventListener('change', (e) => {
        currentLanguage = e.target.value;
    });

    // Toggle Window
    btn.addEventListener('click', () => {
        chatWindow.classList.toggle('open');
        btn.classList.toggle('active');
        if (chatWindow.classList.contains('open')) {
            input.focus();
            btn.innerHTML = '<i class="fa-solid fa-xmark"></i>';
        } else {
            btn.innerHTML = '<i class="fa-solid fa-comment-dots"></i>';
            if (speechSynthesis.speaking) speechSynthesis.cancel();
        }
    });

    // Send Message
    async function sendMessage() {
        const text = input.value.trim();
        if (!text || isGenerating) return;

        // Local command interception: "switch to [plant_name]"
        if (text.toLowerCase().startsWith('switch to ')) {
            const rawPlantName = text.substring(10).trim();
            const requestedName = rawPlantName.toLowerCase().replace(/\s+/g, '_');
            
            // Access CROP_THRESHOLDS from global scope
            const thresholds = (typeof CROP_THRESHOLDS !== 'undefined') ? CROP_THRESHOLDS : {};
            let targetId = Object.keys(thresholds).find(id => 
                id === requestedName || 
                id.includes(requestedName) || 
                requestedName.includes(id)
            );

            input.value = '';
            addMessage('user', text);

            if (targetId) {
                // Trigger the dashboard update
                if (window.updateCropThresholdsFromAI) {
                    window.updateCropThresholdsFromAI(targetId);
                }
                
                setTimeout(() => {
                    addMessage('bot', `🌱 I have switched the dashboard to **${targetId.replace(/_/g, ' ').toUpperCase()}** requirements based on our local database.`);
                }, 500);
            } else {
                setTimeout(() => {
                    const available = Object.keys(thresholds).length > 0 
                        ? Object.keys(thresholds).slice(0, 10).join(', ').replace(/_/g, ' ') + '...'
                        : 'No crops found in database.';
                    addMessage('bot', `🔍 I couldn't find "**${rawPlantName}**" in the local database. <br><br>Available crops include: ${available}`);
                }, 500);
            }
            return; // Bypass AI API call for the "switch to" command
        }

        isGenerating = true;
        abortController = new AbortController();
        updateSendButton();

        input.value = '';
        addMessage('user', text);
        
        const typing = addTypingIndicator();
        messages.scrollTop = messages.scrollHeight;

        try {
            const res = await fetch('/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: abortController.signal,
                body: JSON.stringify({ 
                    message: text,
                    lang: currentLanguage,
                    crop: (typeof currentCrop !== 'undefined') ? currentCrop : 'general'
                })
            });

            if (!res.ok) {
                const errorData = await res.json().catch(() => ({}));
                throw new Error(errorData.reply || `Server error: ${res.status}`);
            }

            const data = await res.json();
            typing.remove();
            
            let reply = data.reply || "";
            
            // Parse hidden commands: switch mode crop name
            const cropMatch = reply.match(/switch mode\s+([\w\s]+)/i);
            if (cropMatch) {
                const requestedName = cropMatch[1].trim().toLowerCase().replace(/\s+/g, '_');
                // Fuzzy match against local database keys
                let targetId = Object.keys(CROP_THRESHOLDS).find(id => 
                    id === requestedName || 
                    id.includes(requestedName) || 
                    requestedName.includes(id)
                );
                
                if (targetId) {
                    if (window.updateCropThresholdsFromAI) window.updateCropThresholdsFromAI(targetId);
                }
            }

            // Parse custom commands [SET_CUSTOM: {"temp":[20,30], ...}]
            const customMatch = reply.match(/\[SET_CUSTOM:\s*(\{.*?\})\]/i);
            if (customMatch) {
                try {
                    const customData = JSON.parse(customMatch[1]);
                    reply = reply.replace(/\[SET_CUSTOM:.*?\]/i, '').trim();
                    if (window.setCustomThresholdsFromAI) window.setCustomThresholdsFromAI(customData);
                } catch(e) { console.error("AI Custom JSON error:", e); }
            }

            addMessage('bot', reply);
        } catch (error) {
            typing.remove();
            if (error.name === 'AbortError') {
                addMessage('bot', '⏹ Generation stopped.');
            } else {
                addMessage('bot', `⚠️ ${error.message || 'Error connecting to cloud.'}`);
                console.error('[Chat] Fetch error:', error);
            }
        } finally {
            isGenerating = false;
            updateSendButton();
            messages.scrollTop = messages.scrollHeight;
        }
    }

    function updateSendButton() {
        if (isGenerating) {
            sendBtn.innerHTML = '<i class="fa-solid fa-stop"></i>';
            sendBtn.style.background = '#ff4757'; // var(--danger)
        } else {
            sendBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
            sendBtn.style.background = '#00ffc3'; // var(--chat-accent)
        }
    }

    sendBtn.addEventListener('click', () => {
        if (isGenerating) {
            if (abortController) abortController.abort();
        } else {
            sendMessage();
        }
    });

    function addMessage(sender, text) {
        const container = document.createElement('div');
        container.className = `chat-msg-container ${sender}`;
        
        let headerHTML = '';
        if (sender === 'bot') {
            headerHTML = `
                <div class="chat-msg-header">
                    <button class="audio-btn" title="Listen"><i class="fa-solid fa-volume-high"></i></button>
                </div>
            `;
        }

        container.innerHTML = `
            ${headerHTML}
            <div class="chat-msg ${sender}">${text.replace(/\n/g, '<br>')}</div>
        `;
        
        if (sender === 'bot') {
            const audioBtn = container.querySelector('.audio-btn');
            audioBtn.addEventListener('click', () => toggleSpeech(text, audioBtn));
        }

        messages.appendChild(container);
        messages.scrollTop = messages.scrollHeight;
    }

    function toggleSpeech(text, btn) {
        if (speechSynthesis.speaking) {
            speechSynthesis.cancel();
            if (btn.classList.contains('playing')) {
                btn.classList.remove('playing');
                btn.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
                return;
            }
        }

        // Reset all buttons
        document.querySelectorAll('.audio-btn').forEach(b => {
            b.classList.remove('playing');
            b.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
        });

        const cleaned = cleanText(text);
        const utterance = new SpeechSynthesisUtterance(cleaned);
        
        // Match voice to language
        const voices = speechSynthesis.getVoices();
        const langPrefix = currentLanguage.split('-')[0];
        utterance.voice = voices.find(v => v.lang.startsWith(langPrefix)) || null;
        utterance.lang = currentLanguage;
        utterance.rate = 0.95; 
        
        utterance.onstart = () => {
            btn.classList.add('playing');
            btn.innerHTML = '<i class="fa-solid fa-circle-stop"></i>';
        };
        
        utterance.onend = () => {
            btn.classList.remove('playing');
            btn.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
        };

        speechSynthesis.speak(utterance);
    }

    function cleanText(text) {
        return text.replace(/([\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDC00-\uDFFF])/g, '')
                   .replace(/[*#_~`>]/g, '') // Remove markdown symbols
                   .replace(/[-–—]+/g, ' ') // Remove all types of dashes (hyphens, en-dash, em-dash)
                   .replace(/([,\.\?\!])/g, '$1 ') // Ensure space after punctuation for better TTS rhythm
                   .replace(/\s+/g, ' ') // Collapse multiple spaces
                   .trim();
    }

    function addTypingIndicator() {
        const typing = document.createElement('div');
        typing.className = 'typing';
        typing.innerHTML = '<span></span><span></span><span></span>';
        messages.appendChild(typing);
        messages.scrollTop = messages.scrollHeight;
        return typing;
    }

    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    // Handle voice list loading (some browsers load async)
    if (speechSynthesis.onvoiceschanged !== undefined) {
        speechSynthesis.onvoiceschanged = () => speechSynthesis.getVoices();
    }
})();
