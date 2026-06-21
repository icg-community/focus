(function () {
    const form = document.getElementById("quick-speech-form");
    if (!form) {
        return;
    }

    const speechText = document.getElementById("speech-text");
    const speechFile = document.getElementById("speech-file");
    const splitLines = document.getElementById("split-lines");
    const browserVoice = document.getElementById("browser-voice");
    const stopSpeech = document.getElementById("stop-speech");
    const previewList = document.getElementById("speech-preview-list");
    const emptyState = document.getElementById("speech-empty-state");
    const visibleStatus = document.getElementById("speech-visible-status");
    const statusRegion = document.getElementById("speech-status");
    const errorRegion = document.getElementById("speech-error");

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

    function renderPreviews(items) {
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
            renderPreviews([]);
            setError("Enter text before preparing a speech preview.");
            speechText.focus();
            return;
        }

        renderPreviews(items);
        setStatus(`${items.length} speech preview ${items.length === 1 ? "item is" : "items are"} ready.`);
    });

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
}());
