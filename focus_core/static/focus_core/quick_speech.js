(function () {
    const form = document.getElementById("quick-speech-form");
    if (!form) {
        return;
    }

    const speechText = document.getElementById("speech-text");
    const speechFile = document.getElementById("speech-file");
    const splitLines = document.getElementById("split-lines");
    const voiceSource = document.getElementById("voice-source");
    const browserVoiceGroup = document.getElementById("browser-voice-group");
    const browserVoice = document.getElementById("browser-voice");
    const starVoiceGroup = document.getElementById("star-voice-group");
    const starVoice = document.getElementById("star-voice");
    const primaryAction = document.getElementById("speech-primary-action");
    const stopSpeech = document.getElementById("stop-speech");
    const previewList = document.getElementById("speech-preview-list");
    const emptyState = document.getElementById("speech-empty-state");
    const visibleStatus = document.getElementById("speech-visible-status");
    const statusRegion = document.getElementById("speech-status");
    const errorRegion = document.getElementById("speech-error");
    const starOption = voiceSource.querySelector('option[value="star"]');
    const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    const audioUrls = [];

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

    function starIsConfigured() {
        return form.dataset.starConfigured === "true";
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

        const selectedVoice = browserVoice.value;
        const voices = window.speechSynthesis.getVoices();
        browserVoice.replaceChildren(new Option("Use the browser default voice", ""));

        voices.forEach((voice) => {
            const option = new Option(`${voice.name} (${voice.lang})`, voice.name);
            browserVoice.add(option);
        });

        if (selectedVoice) {
            browserVoice.value = selectedVoice;
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

            actions.append(playButton);
            article.append(heading, text, actions);
            listItem.append(article);
            previewList.append(listItem);
        });

        const hasItems = items.length > 0;
        previewList.hidden = !hasItems;
        emptyState.hidden = hasItems;
    }

    function audioBlobFromBase64(audioBase64, contentType) {
        const binary = atob(audioBase64);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
            bytes[index] = binary.charCodeAt(index);
        }
        return new Blob([bytes], { type: contentType || "audio/wav" });
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
            const audioUrl = URL.createObjectURL(audioBlobFromBase64(clip.audio_base64, clip.content_type));
            audioUrls.push(audioUrl);
            audio.src = audioUrl;

            const actions = document.createElement("div");
            actions.className = "action-row";

            const downloadLink = document.createElement("a");
            downloadLink.className = "button button--secondary";
            downloadLink.href = audioUrl;
            downloadLink.download = clip.filename || `focus-speech-${itemNumber}.wav`;
            downloadLink.textContent = `Download item ${itemNumber}`;

            actions.append(downloadLink);
            article.append(heading, text, audio, actions);
            listItem.append(article);
            previewList.append(listItem);
        });

        const hasClips = clips.length > 0;
        previewList.hidden = !hasClips;
        emptyState.hidden = hasClips;
    }

    async function loadStarVoices() {
        if (!starIsConfigured()) {
            starOption.disabled = true;
            return;
        }

        try {
            const response = await fetch(form.dataset.starVoicesUrl);
            const data = await response.json();
            if (!response.ok || !data.configured || !data.voices.length) {
                starOption.disabled = true;
                starOption.textContent = data.message || "STAR voices are not available";
                return;
            }

            starVoice.replaceChildren(new Option("Choose a STAR voice", ""));
            data.voices.forEach((voiceName) => {
                starVoice.add(new Option(voiceName, voiceName));
            });
            starOption.disabled = false;
            starOption.textContent = "STAR voices for downloadable audio";
            setStatus(data.message);
        } catch (error) {
            starOption.disabled = true;
            starOption.textContent = "STAR voices could not be loaded";
        }
    }

    async function generateStarAudio(items) {
        if (!starVoice.value) {
            setError("Choose a STAR voice before generating audio.");
            starVoice.focus();
            return;
        }

        primaryAction.disabled = true;
        setStatus(`Generating ${items.length} audio ${items.length === 1 ? "file" : "files"} with STAR.`);
        try {
            const response = await fetch(form.dataset.starGenerateUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify({
                    voice: starVoice.value,
                    items,
                }),
            });
            const data = await response.json();
            if (!response.ok || !data.ok) {
                setError(data.error || "STAR audio could not be generated.");
                return;
            }

            renderAudioClips(data.clips || []);
            setStatus(`${data.clips.length} STAR audio ${data.clips.length === 1 ? "file is" : "files are"} ready.`);
        } catch (error) {
            setError("STAR audio could not be generated.");
        } finally {
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

    stopSpeech.addEventListener("click", function () {
        clearError();
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
            setStatus("Speech preview stopped.");
        }
    });

    populateVoices();
    if ("speechSynthesis" in window) {
        window.speechSynthesis.onvoiceschanged = populateVoices;
    }
    toggleVoiceSource();
    loadStarVoices();
}());
