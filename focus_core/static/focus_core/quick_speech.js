(function () {
    const form = document.getElementById("quick-speech-form");
    if (!form) {
        return;
    }

    const STAR_SOCKET_STORAGE_KEY = "focus.quickSpeech.starSocketUrl";
    const STAR_CLIENT_REVISION = 4;
    const STAR_TIMEOUT_MS = 8000;
    const STAR_MAX_ITEMS = 20;
    const STAR_MAX_ITEM_LENGTH = 1000;
    const STAR_MAX_TOTAL_LENGTH = 5000;

    const speechText = document.getElementById("speech-text");
    const speechFile = document.getElementById("speech-file");
    const splitLines = document.getElementById("split-lines");
    const voiceSource = document.getElementById("voice-source");
    const browserVoiceGroup = document.getElementById("browser-voice-group");
    const browserVoice = document.getElementById("browser-voice");
    const starSocketUrl = document.getElementById("star-socket-url");
    const saveStarSettings = document.getElementById("save-star-settings");
    const testStarConnection = document.getElementById("test-star-connection");
    const forgetStarSettings = document.getElementById("forget-star-settings");
    const starVoiceGroup = document.getElementById("star-voice-group");
    const starVoice = document.getElementById("star-voice");
    const primaryAction = document.getElementById("speech-primary-action");
    const stopSpeech = document.getElementById("stop-speech");
    const previewList = document.getElementById("speech-preview-list");
    const emptyState = document.getElementById("speech-empty-state");
    const speechExportActions = document.getElementById("speech-export-actions");
    const copySpeechText = document.getElementById("copy-speech-text");
    const downloadSpeechText = document.getElementById("download-speech-text");
    const downloadSpeechManifest = document.getElementById("download-speech-manifest");
    const visibleStatus = document.getElementById("speech-visible-status");
    const statusRegion = document.getElementById("speech-status");
    const errorRegion = document.getElementById("speech-error");
    const starOption = voiceSource.querySelector('option[value="star"]');
    const audioUrls = [];
    const exportUrls = [];
    let preparedTextForCopy = "";

    function setStatus(message) {
        visibleStatus.textContent = message;
        statusRegion.textContent = message;
    }

    function setError(message) {
        visibleStatus.textContent = message;
        errorRegion.textContent = message;
    }

    function clearError() {
        errorRegion.textContent = "";
    }

    function releaseAudioUrls() {
        while (audioUrls.length) {
            URL.revokeObjectURL(audioUrls.pop());
        }
    }

    function releaseExportUrls() {
        while (exportUrls.length) {
            URL.revokeObjectURL(exportUrls.pop());
        }
    }

    function updatePreparedExports(items, source) {
        releaseExportUrls();
        const preparedItems = items
            .map((item, index) => ({
                number: index + 1,
                text: String(item.text || item),
                audioFile: item.filename || "",
            }))
            .filter((item) => item.text.trim());

        if (!preparedItems.length) {
            preparedTextForCopy = "";
            speechExportActions.hidden = true;
            downloadSpeechText.removeAttribute("href");
            downloadSpeechManifest.removeAttribute("href");
            return;
        }

        const textContent = preparedItems
            .map((item) => `Item ${item.number}\n${item.text}`)
            .join("\n\n");
        const manifestContent = JSON.stringify({
            createdAt: new Date().toISOString(),
            source,
            items: preparedItems,
        }, null, 2);
        preparedTextForCopy = `${textContent}\n`;
        const textUrl = URL.createObjectURL(new Blob([preparedTextForCopy], { type: "text/plain" }));
        const manifestUrl = URL.createObjectURL(new Blob([`${manifestContent}\n`], { type: "application/json" }));

        exportUrls.push(textUrl, manifestUrl);
        downloadSpeechText.href = textUrl;
        downloadSpeechManifest.href = manifestUrl;
        speechExportActions.hidden = false;
    }

    function copyTextWithFallback(text) {
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.setAttribute("readonly", "");
        textArea.style.position = "fixed";
        textArea.style.top = "0";
        textArea.style.opacity = "0";
        document.body.append(textArea);
        textArea.select();

        const copied = document.execCommand("copy");
        textArea.remove();
        if (!copied) {
            throw new Error("Prepared text could not be copied.");
        }
    }

    async function copyTextToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
        }

        copyTextWithFallback(text);
    }

    async function copyPreparedTextToClipboard() {
        clearError();
        if (!preparedTextForCopy.trim()) {
            setError("Prepare speech before copying text.");
            return;
        }

        try {
            await copyTextToClipboard(preparedTextForCopy);
            setStatus("Prepared text copied to clipboard.");
        } catch (error) {
            setError(error.message || "Prepared text could not be copied.");
        }
    }

    async function copyItemTextToClipboard(text, itemNumber) {
        clearError();
        if (!String(text || "").trim()) {
            setError(`Item ${itemNumber} has no text to copy.`);
            return;
        }

        try {
            await copyTextToClipboard(text);
            setStatus(`Item ${itemNumber} text copied to clipboard.`);
        } catch (error) {
            setError(error.message || `Item ${itemNumber} text could not be copied.`);
        }
    }

    function shortLabel(text) {
        const collapsed = text.replace(/\s+/g, " ").trim();
        if (collapsed.length <= 60) {
            return collapsed;
        }
        return `${collapsed.slice(0, 57)}...`;
    }

    function getItems() {
        const text = speechText.value.trim();
        if (!text) {
            return [];
        }

        if (!splitLines.checked) {
            return [text];
        }

        return text
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean);
    }

    function normalizeStarLine(text) {
        return String(text || "").replace(/\s+/g, " ").trim();
    }

    function validateStarItems(items) {
        const normalizedItems = items.map(normalizeStarLine).filter(Boolean);
        const totalLength = normalizedItems.reduce((total, item) => total + item.length, 0);

        if (!normalizedItems.length) {
            throw new Error("Enter text before generating audio.");
        }
        if (normalizedItems.length > STAR_MAX_ITEMS) {
            throw new Error(`Generate ${STAR_MAX_ITEMS} items or fewer at a time.`);
        }
        if (totalLength > STAR_MAX_TOTAL_LENGTH) {
            throw new Error("Shorten the speech text before generating audio.");
        }
        if (normalizedItems.some((item) => item.length > STAR_MAX_ITEM_LENGTH)) {
            throw new Error(`Keep each speech item to ${STAR_MAX_ITEM_LENGTH} characters or fewer.`);
        }

        return normalizedItems;
    }

    function isLocalHost(hostname) {
        return ["localhost", "127.0.0.1", "::1"].includes(hostname);
    }

    function validateStarSocketUrl(value) {
        const trimmed = value.trim();
        if (!trimmed) {
            throw new Error("Enter a STAR socket address first.");
        }

        let parsedUrl;
        try {
            parsedUrl = new URL(trimmed);
        } catch (error) {
            throw new Error("Enter a valid STAR socket address.");
        }

        if (!["ws:", "wss:"].includes(parsedUrl.protocol) || !parsedUrl.host) {
            throw new Error("STAR socket addresses must start with ws:// or wss://.");
        }

        if (window.location.protocol === "https:" && parsedUrl.protocol === "ws:" && !isLocalHost(parsedUrl.hostname)) {
            throw new Error("Hosted FOCUS pages need wss:// STAR sockets unless the address is localhost.");
        }

        return parsedUrl.toString();
    }

    function storedStarSocketUrl() {
        try {
            return localStorage.getItem(STAR_SOCKET_STORAGE_KEY) || "";
        } catch (error) {
            return "";
        }
    }

    function saveStarSocketUrl(socketUrl) {
        try {
            localStorage.setItem(STAR_SOCKET_STORAGE_KEY, socketUrl);
            return true;
        } catch (error) {
            setError("This browser could not save the STAR settings.");
            return false;
        }
    }

    function forgetStarSocketUrl() {
        try {
            localStorage.removeItem(STAR_SOCKET_STORAGE_KEY);
            return true;
        } catch (error) {
            setError("This browser could not forget the STAR settings.");
            return false;
        }
    }

    function resetStarVoices(message) {
        starVoice.replaceChildren(new Option(message, ""));
        starOption.disabled = true;
        starOption.textContent = "STAR audio generation needs a saved socket";
        if (voiceSource.value === "star") {
            voiceSource.value = "browser";
        }
        toggleVoiceSource();
    }

    function toggleVoiceSource() {
        const useStar = voiceSource.value === "star";
        browserVoiceGroup.hidden = useStar;
        starVoiceGroup.hidden = !useStar;
        stopSpeech.hidden = useStar;
        primaryAction.textContent = useStar ? "Generate STAR audio" : "Prepare speech preview";
    }

    function populateVoices() {
        if (!("speechSynthesis" in window)) {
            browserVoice.disabled = true;
            setError("Browser speech preview is not supported in this browser.");
            return;
        }

        const selectedVoiceName = browserVoice.value;
        const voices = window.speechSynthesis.getVoices();
        browserVoice.replaceChildren(new Option("Use the browser default voice", ""));

        voices.forEach((voice) => {
            const option = new Option(`${voice.name} (${voice.lang})`, voice.name);
            browserVoice.add(option);
        });

        if (selectedVoiceName) {
            browserVoice.value = selectedVoiceName;
        }
    }

    function selectedVoice() {
        if (!("speechSynthesis" in window)) {
            return null;
        }
        return window.speechSynthesis
            .getVoices()
            .find((voice) => voice.name === browserVoice.value) || null;
    }

    function speak(text, itemNumber) {
        clearError();
        if (!("speechSynthesis" in window)) {
            setError("Browser speech preview is not supported in this browser.");
            return;
        }

        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        const voice = selectedVoice();
        if (voice) {
            utterance.voice = voice;
        }

        utterance.onstart = function () {
            setStatus(`Speaking item ${itemNumber}.`);
        };
        utterance.onend = function () {
            setStatus(`Finished item ${itemNumber}.`);
        };
        utterance.onerror = function () {
            setError(`Item ${itemNumber} could not be spoken by this browser.`);
        };
        window.speechSynthesis.speak(utterance);
    }

    function renderBrowserPreviews(items) {
        releaseAudioUrls();
        previewList.replaceChildren();
        items.forEach((item, index) => {
            const itemNumber = index + 1;
            const listItem = document.createElement("li");
            listItem.className = "speech-preview-item";

            const article = document.createElement("article");
            const heading = document.createElement("h3");
            heading.textContent = `Item ${itemNumber}`;

            const text = document.createElement("p");
            text.textContent = item;

            const actions = document.createElement("div");
            actions.className = "action-row";

            const playButton = document.createElement("button");
            playButton.type = "button";
            playButton.className = "button";
            playButton.textContent = `Play item ${itemNumber}`;
            playButton.setAttribute("aria-label", `Play item ${itemNumber}: ${shortLabel(item)}`);
            playButton.addEventListener("click", function () {
                speak(item, itemNumber);
            });

            const copyButton = document.createElement("button");
            copyButton.type = "button";
            copyButton.className = "button button--secondary";
            copyButton.textContent = `Copy item ${itemNumber} text`;
            copyButton.setAttribute("aria-label", `Copy item ${itemNumber} text: ${shortLabel(item)}`);
            copyButton.addEventListener("click", function () {
                copyItemTextToClipboard(item, itemNumber);
            });

            actions.append(playButton, copyButton);
            article.append(heading, text, actions);
            listItem.append(article);
            previewList.append(listItem);
        });

        const hasItems = items.length > 0;
        previewList.hidden = !hasItems;
        emptyState.hidden = hasItems;
        updatePreparedExports(items, "browser-preview");
    }

    function renderAudioClips(clips) {
        releaseAudioUrls();
        previewList.replaceChildren();
        clips.forEach((clip, index) => {
            const itemNumber = index + 1;
            const listItem = document.createElement("li");
            listItem.className = "speech-preview-item";

            const article = document.createElement("article");
            const heading = document.createElement("h3");
            heading.textContent = `Item ${itemNumber}`;

            const text = document.createElement("p");
            text.textContent = clip.text || "";

            const audio = document.createElement("audio");
            audio.controls = true;
            audio.setAttribute("aria-label", `Generated audio for item ${itemNumber}`);
            const audioUrl = URL.createObjectURL(clip.audioBlob);
            audioUrls.push(audioUrl);
            audio.src = audioUrl;

            const actions = document.createElement("div");
            actions.className = "action-row";

            const downloadLink = document.createElement("a");
            downloadLink.className = "button button--secondary";
            downloadLink.href = audioUrl;
            downloadLink.download = clip.filename || `focus-speech-${itemNumber}.wav`;
            downloadLink.textContent = `Download item ${itemNumber}`;

            const copyButton = document.createElement("button");
            copyButton.type = "button";
            copyButton.className = "button button--secondary";
            copyButton.textContent = `Copy item ${itemNumber} text`;
            copyButton.setAttribute("aria-label", `Copy item ${itemNumber} text: ${shortLabel(clip.text || "")}`);
            copyButton.addEventListener("click", function () {
                copyItemTextToClipboard(clip.text || "", itemNumber);
            });

            actions.append(copyButton, downloadLink);
            article.append(heading, text, audio, actions);
            listItem.append(article);
            previewList.append(listItem);
        });

        const hasClips = clips.length > 0;
        previewList.hidden = !hasClips;
        emptyState.hidden = hasClips;
        updatePreparedExports(clips, "star-audio");
    }

    function openStarSocket(socketUrl) {
        return new Promise((resolve, reject) => {
            let websocket;
            const timeout = window.setTimeout(function () {
                if (websocket) {
                    websocket.close();
                }
                reject(new Error("STAR connection timed out."));
            }, STAR_TIMEOUT_MS);

            try {
                websocket = new WebSocket(socketUrl);
                websocket.binaryType = "arraybuffer";
            } catch (error) {
                window.clearTimeout(timeout);
                reject(new Error("STAR connection could not be opened."));
                return;
            }

            websocket.addEventListener("open", function () {
                window.clearTimeout(timeout);
                resolve(websocket);
            }, { once: true });
            websocket.addEventListener("error", function () {
                window.clearTimeout(timeout);
                reject(new Error("STAR connection could not be opened."));
            }, { once: true });
        });
    }

    function receiveStarMessage(websocket) {
        return new Promise((resolve, reject) => {
            const timeout = window.setTimeout(cleanUpAndReject, STAR_TIMEOUT_MS);

            function cleanUp() {
                window.clearTimeout(timeout);
                websocket.removeEventListener("message", onMessage);
                websocket.removeEventListener("close", onClose);
                websocket.removeEventListener("error", onError);
            }

            function cleanUpAndReject() {
                cleanUp();
                reject(new Error("STAR did not respond in time."));
            }

            function onMessage(event) {
                cleanUp();
                resolve(event.data);
            }

            function onClose() {
                cleanUp();
                reject(new Error("STAR connection closed before it finished."));
            }

            function onError() {
                cleanUp();
                reject(new Error("STAR connection failed."));
            }

            websocket.addEventListener("message", onMessage);
            websocket.addEventListener("close", onClose);
            websocket.addEventListener("error", onError);
        });
    }

    async function dataAsText(data) {
        if (typeof data === "string") {
            return data;
        }
        if (data instanceof Blob) {
            return data.text();
        }
        if (data instanceof ArrayBuffer) {
            return new TextDecoder().decode(data);
        }
        return "";
    }

    async function dataAsArrayBuffer(data) {
        if (data instanceof ArrayBuffer) {
            return data;
        }
        if (data instanceof Blob) {
            return data.arrayBuffer();
        }
        return null;
    }

    async function receiveStarJson(websocket) {
        const data = await receiveStarMessage(websocket);
        const text = await dataAsText(data);
        return JSON.parse(text);
    }

    function cleanAudioExtension(extension) {
        const cleaned = String(extension || "wav").replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
        return cleaned || "wav";
    }

    function parseStarAudioMessage(buffer, fallbackOrder) {
        if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 2) {
            return null;
        }

        const metadataLength = new DataView(buffer).getUint16(0, true);
        const metadataEnd = 2 + metadataLength;
        if (metadataEnd >= buffer.byteLength) {
            return null;
        }

        const metadataText = new TextDecoder().decode(buffer.slice(2, metadataEnd));
        let metadata = { id: metadataText, extension: "wav" };
        try {
            metadata = JSON.parse(metadataText);
        } catch (error) {
            metadata = { id: metadataText, extension: "wav" };
        }

        const requestId = String(metadata.id || "");
        const parsedOrder = Number.parseInt(requestId.split("_").pop(), 10);
        const extension = cleanAudioExtension(metadata.extension);
        const contentType = extension === "mp3" ? "audio/mpeg" : `audio/${extension}`;
        return {
            order: Number.isNaN(parsedOrder) ? fallbackOrder : parsedOrder,
            extension,
            audioBlob: new Blob([buffer.slice(metadataEnd)], { type: contentType }),
        };
    }

    async function loadStarVoices(socketUrl) {
        let websocket;
        try {
            websocket = await openStarSocket(socketUrl);
            websocket.send(JSON.stringify({ user: STAR_CLIENT_REVISION }));
            const data = await receiveStarJson(websocket);
            const voices = Array.isArray(data.voices)
                ? data.voices.map((voice) => String(voice).trim()).filter(Boolean).sort()
                : [];

            if (!voices.length) {
                throw new Error("No STAR voices were returned.");
            }

            starVoice.replaceChildren(new Option("Choose a STAR voice", ""));
            voices.forEach((voiceName) => {
                starVoice.add(new Option(voiceName, voiceName));
            });
            starOption.disabled = false;
            starOption.textContent = "STAR voices for downloadable audio";
            return voices;
        } finally {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.close();
            }
        }
    }

    async function generateStarAudio(items) {
        if (!starVoice.value) {
            setError("Choose a STAR voice before generating audio.");
            starVoice.focus();
            return;
        }

        let normalizedItems;
        let socketUrl;
        try {
            normalizedItems = validateStarItems(items);
            socketUrl = validateStarSocketUrl(starSocketUrl.value || storedStarSocketUrl());
        } catch (error) {
            setError(error.message);
            return;
        }

        const requestLines = normalizedItems.map((item) => `${starVoice.value}: ${item}`);
        let websocket;
        primaryAction.disabled = true;
        setStatus(`Generating ${normalizedItems.length} audio ${normalizedItems.length === 1 ? "file" : "files"} with STAR.`);
        try {
            websocket = await openStarSocket(socketUrl);
            websocket.send(JSON.stringify({
                user: STAR_CLIENT_REVISION,
                request: requestLines,
            }));

            const clips = [];
            while (clips.length < requestLines.length) {
                const message = await receiveStarMessage(websocket);
                if (typeof message === "string") {
                    const data = JSON.parse(message);
                    if (data.abort) {
                        throw new Error(data.status || "STAR audio generation failed.");
                    }
                    continue;
                }

                const buffer = await dataAsArrayBuffer(message);
                const clip = parseStarAudioMessage(buffer, clips.length);
                if (clip) {
                    clips.push(clip);
                }
            }

            clips.sort((first, second) => first.order - second.order);
            renderAudioClips(clips.map((clip, index) => ({
                ...clip,
                filename: `focus-speech-${String(index + 1).padStart(3, "0")}.${clip.extension}`,
                text: normalizedItems[index] || "",
            })));
            setStatus(`${clips.length} STAR audio ${clips.length === 1 ? "file is" : "files are"} ready.`);
        } catch (error) {
            setError(error.message || "STAR audio could not be generated.");
        } finally {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.close();
            }
            primaryAction.disabled = false;
        }
    }

    speechFile.addEventListener("change", function () {
        clearError();
        const file = speechFile.files[0];
        if (!file) {
            return;
        }

        const reader = new FileReader();
        reader.onload = function () {
            speechText.value = reader.result || "";
            setStatus(`Loaded ${file.name}.`);
            speechText.focus();
        };
        reader.onerror = function () {
            setError(`${file.name} could not be loaded.`);
        };
        reader.readAsText(file);
    });

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        clearError();

        const items = getItems();
        if (!items.length) {
            renderBrowserPreviews([]);
            setError("Enter text before preparing a speech preview.");
            speechText.focus();
            return;
        }

        if (voiceSource.value === "star") {
            generateStarAudio(items);
            return;
        }

        renderBrowserPreviews(items);
        setStatus(`${items.length} speech preview ${items.length === 1 ? "item is" : "items are"} ready.`);
    });

    voiceSource.addEventListener("change", toggleVoiceSource);

    copySpeechText.addEventListener("click", copyPreparedTextToClipboard);

    saveStarSettings.addEventListener("click", function () {
        clearError();
        try {
            const socketUrl = validateStarSocketUrl(starSocketUrl.value);
            if (saveStarSocketUrl(socketUrl)) {
                starSocketUrl.value = socketUrl;
                resetStarVoices("Test STAR connection to load voices");
                setStatus("STAR settings saved in this browser.");
            }
        } catch (error) {
            resetStarVoices("Test STAR connection to load voices");
            setError(error.message);
            starSocketUrl.focus();
        }
    });

    testStarConnection.addEventListener("click", async function () {
        clearError();
        let socketUrl;
        try {
            socketUrl = validateStarSocketUrl(starSocketUrl.value || storedStarSocketUrl());
        } catch (error) {
            resetStarVoices("Test STAR connection to load voices");
            setError(error.message);
            starSocketUrl.focus();
            return;
        }

        testStarConnection.disabled = true;
        resetStarVoices("STAR voices are loading");
        setStatus("Testing STAR connection.");
        try {
            const voices = await loadStarVoices(socketUrl);
            const saved = saveStarSocketUrl(socketUrl);
            starSocketUrl.value = socketUrl;
            if (saved) {
                setStatus(`${voices.length} STAR voice${voices.length === 1 ? "" : "s"} available.`);
            }
        } catch (error) {
            resetStarVoices("Test STAR connection to load voices");
            setError(error.message || "STAR voices could not be loaded.");
        } finally {
            testStarConnection.disabled = false;
        }
    });

    forgetStarSettings.addEventListener("click", function () {
        clearError();
        const forgotten = forgetStarSocketUrl();
        starSocketUrl.value = "";
        resetStarVoices("Test STAR connection to load voices");
        if (forgotten) {
            setStatus("STAR settings forgotten in this browser.");
        }
    });

    stopSpeech.addEventListener("click", function () {
        clearError();
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
            setStatus("Speech preview stopped.");
        }
    });

    const savedSocketUrl = storedStarSocketUrl();
    if (savedSocketUrl) {
        starSocketUrl.value = savedSocketUrl;
        setStatus("STAR settings loaded from this browser. Test the connection to load voices.");
    }

    resetStarVoices("Test STAR connection to load voices");
    populateVoices();
    if ("speechSynthesis" in window) {
        window.speechSynthesis.onvoiceschanged = populateVoices;
    }
    toggleVoiceSource();
}());
