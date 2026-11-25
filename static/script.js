const $ = function (id) {
    return document.getElementById(id);
};

const consoleBox = $("console");
const statusBadge = $("statusBadge");
const cmdInput = $("cmdInput");

const serverList = $("serverList");
const newName = $("newName");
const newVersion = $("newVersion");
const newRam = $("newRam");
const newLoader = $("newLoader");
const modSearch = $("modSearch");
const modSearchBtn = $("modSearchBtn");
const modResults = $("modResults");
const installedMods = $("installedMods");
const newType = $("newType");
const newSeed = $("newSeed");
const createBtn = $("createBtn");
const createMsg = $("createMsg");

const curName = $("curName");
const versionInput = $("versionInput");
const versionNote = $("versionNote");
const ramSelect = $("ramSelect");

const setupBtn = $("setupBtn");
const startBtn = $("startBtn");
const stopBtn = $("stopBtn");
const restartBtn = $("restartBtn");
const sendBtn = $("sendBtn");

const propsForm = $("propsForm");
const propsSaveBtn = $("propsSaveBtn");
const propsReloadBtn = $("propsReloadBtn");
const propsMsg = $("propsMsg");

const pluginSearch = $("pluginSearch");
const pluginSearchBtn = $("pluginSearchBtn");
const pluginResults = $("pluginResults");
const installedPlugins = $("installedPlugins");

let lastLogId = 0;
let propSchema = [];
let servers = [];
let selectedId = "";
let runId = "";
let settingUpId = "";
let appliedId = null;
let lastListSig = "";
let lastPluginSig = "";
    loadMods();
let typesFilled = false;
let pluginHits = [];
let modHits = [];
const selectedPluginIds = new Set();
const selectedModIds = new Set();


// ---------- Console me line add karna ----------
function addLine(text) {
    const nearBottom =
        consoleBox.scrollHeight - consoleBox.scrollTop - consoleBox.clientHeight < 60;

    consoleBox.appendChild(document.createTextNode(text + "\n"));

    // Console bahut lamba na ho jaye
    while (consoleBox.childNodes.length > 1500) {
        consoleBox.removeChild(consoleBox.firstChild);
    }

    if (nearBottom) {
        consoleBox.scrollTop = consoleBox.scrollHeight;
    }
}


// ---------- CSRF token + secure POST requests ----------
let csrfToken = "";
let csrfTokenPromise = null;

async function getCsrfToken(forceRefresh) {
    if (csrfToken && !forceRefresh) return csrfToken;
    if (csrfTokenPromise && !forceRefresh) return csrfTokenPromise;

    csrfTokenPromise = (async function () {
        const res = await fetch("/api/csrf-token", {
            method: "GET",
            credentials: "same-origin",
            cache: "no-store"
        });
        const data = await res.json();
        if (!res.ok || !data.ok || !data.csrf_token) {
            throw new Error(data.message || "CSRF token nahi mila.");
        }
        csrfToken = data.csrf_token;
        return csrfToken;
    })();

    try {
        return await csrfTokenPromise;
    } finally {
        csrfTokenPromise = null;
    }
}

// ---------- Server ko CSRF-protected POST request bhejna ----------
async function postJSON(url, data) {
    try {
        const token = await getCsrfToken(false);
        const res = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": token
            },
            body: JSON.stringify(data || {})
        });
        const result = await res.json();
        if (res.status === 403) {
            csrfToken = "";
            return { ok: false, message: result.message || "Security token expire hua. Page refresh karo." };
        }
        return result;
    } catch (err) {
        return { ok: false, message: "Panel se connection nahi ho paya: " + err };
    }
}


// ---------- Dropdown me value set karna (na ho to option jod dena) ----------
function setSelectValue(select, value) {
    let found = false;
    for (let i = 0; i < select.options.length; i++) {
        if (select.options[i].value === value) {
            found = true;
            break;
        }
    }
    if (!found) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.insertBefore(option, select.firstChild);
    }
    select.value = value;
}


function serverById(id) {
    for (let i = 0; i < servers.length; i++) {
        if (servers[i].id === id) {
            return servers[i];
        }
    }
    return null;
}


// ---------- Minecraft versions ki list lana ----------
async function loadVersions() {
    try {
        const res = await fetch("/api/versions");
        const data = await res.json();
        if (!data.ok || data.versions.length === 0) {
            return;
        }

        [versionInput, newVersion].forEach(function (select) {
            select.textContent = "";
            data.versions.forEach(function (v) {
                const option = document.createElement("option");
                option.value = v;
                option.textContent = v;
                select.appendChild(option);
            });
        });

        if (data.versions.indexOf("1.21.8") !== -1) {
            newVersion.value = "1.21.8";
            versionInput.value = "1.21.8";
        }

        if (data.note) {
            versionNote.textContent = versionNote.textContent + " " + data.note;
        }
    } catch (err) {
        // list na aaye to 1.21.8 wala option rehne do
    }
}


// ---------- Servers ki list lana aur dikhana ----------
async function loadServers(force) {
    try {
        const res = await fetch("/api/servers");
        const data = await res.json();
        if (!data.ok) {
            return;
        }

        servers = data.servers;
        selectedId = data.selected;
        runId = data.run_id;

        if (!typesFilled) {
            data.types.forEach(function (t) {
                const option = document.createElement("option");
                option.value = t.value;
                option.textContent = t.label;
                newType.appendChild(option);
            });
            typesFilled = true;
        }

        renderServerList();

        if (force || selectedId !== appliedId) {
            appliedId = selectedId;
            applySelection(serverById(selectedId));
        }
    } catch (err) {
        // agli baar try karenge
    }
}


function renderServerList() {
    const sig = JSON.stringify([servers, selectedId, runId, settingUpId]);
    if (sig === lastListSig) {
        return;
    }
    lastListSig = sig;

    serverList.textContent = "";

    if (servers.length === 0) {
        serverList.textContent = "Abhi koi server nahi hai. Neeche se naya server banao.";
        return;
    }

    servers.forEach(function (s) {
        const item = document.createElement("div");
        item.className = "plugin-item" + (s.id === selectedId ? " selected-row" : "");

        const info = document.createElement("div");
        info.className = "plugin-info";

        const title = document.createElement("strong");
        title.textContent = s.name;

        const meta = document.createElement("small");
        let stateText = "Stopped";
        if (s.running) {
            stateText = "Running";
        } else if (settingUpId === s.id) {
            stateText = "Setup ho raha hai...";
        } else if (!s.ready) {
            stateText = "Setup baaki (Setup / Update dabao)";
        }
        meta.textContent = (s.loader || "paper").toUpperCase() + "  |  " + s.version + "  |  " + s.ram + " RAM  |  " + stateText;

        info.appendChild(title);
        info.appendChild(meta);

        const buttons = document.createElement("div");
        buttons.className = "item-buttons";

        if (s.id === selectedId) {
            const badge = document.createElement("span");
            badge.className = "badge running";
            badge.textContent = "SELECTED";
            buttons.appendChild(badge);
        } else {
            const selBtn = document.createElement("button");
            selBtn.className = "btn blue";
            selBtn.textContent = "Select";
            selBtn.addEventListener("click", function () {
                selectServer(s.id);
            });
            buttons.appendChild(selBtn);
        }

        const delBtn = document.createElement("button");
        delBtn.className = "btn red";
        delBtn.textContent = "Delete";
        delBtn.disabled = s.running || settingUpId === s.id;
        delBtn.addEventListener("click", function () {
            deleteServer(s);
        });
        buttons.appendChild(delBtn);

        item.appendChild(info);
        item.appendChild(buttons);
        serverList.appendChild(item);
    });
}


// ---------- Chuna hua server badalne par form theek karna ----------
function applySelection(cur) {
    pluginResults.textContent = "";
    lastPluginSig = "";

    if (!cur) {
        curName.textContent = "Koi server nahi";
        propsForm.textContent = "";
        installedPlugins.textContent = "";
        propsMsg.textContent = "Pehle server chuno ya naya banao.";
        return;
    }

    curName.textContent = cur.name;
    setSelectValue(versionInput, cur.version);
    setSelectValue(ramSelect, cur.ram);
    loadProps();
    loadInstalled();
    loadMods();
}


async function selectServer(id) {
    const result = await postJSON("/api/servers/select", { id: id });
    addLine("[Panel] " + result.message);
    await loadServers(true);
    fetchStatus();
}


async function deleteServer(s) {
    if (s.running || settingUpId === s.id) {
        addLine("[Panel] Pehle server Stop karo aur setup complete hone do.");
        return;
    }
    const accepted = window.confirm(
        "Kya tum '" + s.name + "' ko permanently delete karna chahte ho?\n\n" +
        "Isse uski world, plugins, mods aur settings bhi delete ho jayengi.\n" +
        "Yeh action undo nahi ho sakta."
    );
    if (!accepted) return;

    const row = serverList.querySelector(".selected-row");
    const result = await postJSON("/api/servers/delete", { id: s.id, confirm: true });
    addLine("[Panel] " + (result.message || "Server delete request complete nahi hui."));
    if (!result.ok) {
        window.alert(result.message || "Server delete nahi ho paya.");
        return;
    }
    lastListSig = "";
    appliedId = null;
    await loadServers(true);
    fetchStatus();
}


// ---------- Naya server banana ----------
createBtn.addEventListener("click", async function () {
    const name = newName.value.trim();
    if (name === "") {
        createMsg.textContent = "Server ka naam likho.";
        return;
    }
    createBtn.disabled = true;
    createMsg.textContent = "Ban raha hai...";
    const result = await postJSON("/api/servers/create", {
        name: name,
        version: newVersion.value,
        ram: newRam.value,
        loader: newLoader.value,
        type: newType.value,
        seed: newSeed.value
    });
    createMsg.textContent = result.message;
    addLine("[Panel] " + result.message);
    if (result.ok) {
        newName.value = "";
        newSeed.value = "";
    }
    await loadServers(true);
    fetchStatus();
});


// ---------- Logs lana (har 1 second) ----------
async function fetchLogs() {
    try {
        const res = await fetch("/api/logs?since=" + lastLogId);
        const data = await res.json();
        data.logs.forEach(function (item) {
            addLine(item.text);
        });
        lastLogId = data.last;
    } catch (err) {
        // connection fail ho to chup-chaap agli baar try karenge
    }
}


// ---------- Status lana (har 2 second) ----------
async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();

        runId = data.run_id;
        settingUpId = data.setup_id;
        selectedId = data.selected;

        const selRunning = data.running && data.run_id === data.selected;
        const hasServer = data.selected !== "";

        if (data.setting_up) {
            statusBadge.textContent = "Setting up...";
            statusBadge.className = "badge setup";
        } else if (data.running) {
            const r = serverById(data.run_id);
            statusBadge.textContent = "Running: " + (r ? r.name : "server");
            statusBadge.className = "badge running";
        } else {
            statusBadge.textContent = "Stopped";
            statusBadge.className = "badge stopped";
        }

        setupBtn.disabled = !hasServer || data.setting_up || selRunning;
        startBtn.disabled = !hasServer || data.setting_up || data.running;
        stopBtn.disabled = !data.running;
        restartBtn.disabled = !selRunning;
        propsSaveBtn.disabled = !hasServer || selRunning;
        createBtn.disabled = data.setting_up;

        renderServerList();
    } catch (err) {
        statusBadge.textContent = "Panel offline";
        statusBadge.className = "badge stopped";
    }
}


// ---------- Buttons ----------
setupBtn.addEventListener("click", async function () {
    const result = await postJSON("/api/setup", { version: versionInput.value });
    if (!result.ok) {
        addLine("[Panel] " + result.message);
    }
    fetchStatus();
});

startBtn.addEventListener("click", async function () {
    const result = await postJSON("/api/start", { ram: ramSelect.value });
    if (!result.ok) {
        addLine("[Panel] " + result.message);
    }
    fetchStatus();
});

stopBtn.addEventListener("click", async function () {
    const result = await postJSON("/api/stop", {});
    if (!result.ok) {
        addLine("[Panel] " + result.message);
    }
});

restartBtn.addEventListener("click", async function () {
    const result = await postJSON("/api/restart", { ram: ramSelect.value });
    if (!result.ok) {
        addLine("[Panel] " + result.message);
    }
});


// ---------- Command bhejna ----------
async function sendCommand() {
    const cmd = cmdInput.value.trim();
    if (cmd === "") {
        return;
    }
    const result = await postJSON("/api/command", { cmd: cmd });
    if (!result.ok) {
        addLine("[Panel] " + result.message);
    }
    cmdInput.value = "";
}

sendBtn.addEventListener("click", sendCommand);

cmdInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
        sendCommand();
    }
});


// ---------- Server Properties: form banana ----------
function buildPropsForm(schema, values) {
    propsForm.textContent = "";

    schema.forEach(function (item) {
        const row = document.createElement("div");
        row.className = "prop-row";

        const label = document.createElement("label");
        label.textContent = item.label;
        label.setAttribute("for", "prop_" + item.key);

        let control;
        const current = values[item.key];

        if (item.type === "bool") {
            control = document.createElement("input");
            control.type = "checkbox";
            control.checked = (current === "true");
        } else if (item.type === "select") {
            control = document.createElement("select");
            item.options.forEach(function (opt) {
                const option = document.createElement("option");
                option.value = opt;
                option.textContent = opt;
                control.appendChild(option);
            });
            if (current !== undefined) {
                control.value = current;
            }
        } else {
            control = document.createElement("input");
            control.type = "text";
            control.value = (current !== undefined) ? current : "";
            if (item.type === "int") {
                control.setAttribute("inputmode", "numeric");
            }
        }

        control.id = "prop_" + item.key;
        row.appendChild(label);
        row.appendChild(control);
        propsForm.appendChild(row);
    });
}


// ---------- Server Properties: file se lana ----------
async function loadProps() {
    propsMsg.textContent = "";
    try {
        const res = await fetch("/api/props");
        const data = await res.json();
        if (!data.ok) {
            propsForm.textContent = "";
            propsMsg.textContent = data.message;
            return;
        }
        propSchema = data.schema;
        buildPropsForm(data.schema, data.values);
    } catch (err) {
        propsMsg.textContent = "Properties nahi aayi: " + err;
    }
}


// ---------- Server Properties: save karna ----------
async function saveProps() {
    if (propSchema.length === 0) {
        propsMsg.textContent = "Pehle properties load hone do (Reload dabao).";
        return;
    }

    const values = {};
    propSchema.forEach(function (item) {
        const el = $("prop_" + item.key);
        if (!el) {
            return;
        }
        if (item.type === "bool") {
            values[item.key] = el.checked;
        } else {
            values[item.key] = el.value;
        }
    });

    const result = await postJSON("/api/props/save", { values: values });
    addLine("[Panel] " + result.message);
    if (result.ok) {
        await loadProps();
    }
    propsMsg.textContent = result.message;
}

propsSaveBtn.addEventListener("click", saveProps);
propsReloadBtn.addEventListener("click", loadProps);


// ---------- Plugins: search ----------
async function searchPlugins() {
    const q = pluginSearch.value.trim();
    if (q === "") {
        return;
    }
    pluginResults.textContent = "Dhoondh raha hoon...";
    try {
        const res = await fetch("/api/plugins/search?q=" + encodeURIComponent(q));
        const data = await res.json();
        renderResults(data);
    } catch (err) {
        pluginResults.textContent = "Search fail ho gaya: " + err;
    }
}

function updateBulkSelection(kind) {
    const isPlugin = kind === "plugin";
    const selected = isPlugin ? selectedPluginIds : selectedModIds;
    const hits = isPlugin ? pluginHits : modHits;
    const count = $(isPlugin ? "pluginSelectedCount" : "modSelectedCount");
    const all = $(isPlugin ? "pluginSelectAll" : "modSelectAll");
    const button = $(isPlugin ? "pluginBulkInstall" : "modBulkInstall");
    if (count) count.textContent = selected.size + " selected";
    if (button) button.disabled = selected.size === 0;
    if (all) {
        all.checked = hits.length > 0 && hits.every(hit => selected.has(hit.id));
        all.indeterminate = selected.size > 0 && !all.checked;
    }
}

function renderResults(data) {
    pluginResults.textContent = "";
    pluginHits = data && data.ok ? data.hits : [];
    selectedPluginIds.clear();
    updateBulkSelection("plugin");
    if (!data.ok) { pluginResults.textContent = data.message; return; }
    if (data.hits.length === 0) { pluginResults.textContent = "Kuch nahi mila. Doosra naam try karo."; return; }
    data.hits.forEach(function (hit) {
        const item = document.createElement("div"); item.className = "plugin-item result-item";
        const check = document.createElement("input"); check.type = "checkbox"; check.className = "result-check";
        check.setAttribute("aria-label", hit.title + " select karo");
        check.addEventListener("change", function () {
            if (check.checked) selectedPluginIds.add(hit.id); else selectedPluginIds.delete(hit.id);
            updateBulkSelection("plugin");
        });
        const info = document.createElement("div"); info.className = "plugin-info";
        const title = document.createElement("strong"); title.textContent = hit.title;
        const desc = document.createElement("span"); desc.textContent = hit.description;
        const meta = document.createElement("small"); meta.textContent = "by " + hit.author + " | " + hit.downloads.toLocaleString() + " downloads";
        info.append(title, desc, meta);
        const btn = document.createElement("button"); btn.className = "btn green"; btn.textContent = "Install";
        btn.addEventListener("click", function () { installPlugin(hit.id, btn); });
        item.append(check, info, btn); pluginResults.appendChild(item);
    });
}

// ---------- Plugins: install ----------
async function installPlugin(projectId, btn) {
    btn.disabled = true;
    btn.textContent = "Installing...";
    const result = await postJSON("/api/plugins/install", { project_id: projectId });
    addLine("[Panel] " + result.message);
    if (result.ok) {
        btn.textContent = "Installed";
    } else {
        btn.textContent = "Install";
        btn.disabled = false;
    }
    loadInstalled();
}


// ---------- Plugins: installed list ----------
async function loadInstalled() {
    try {
        const res = await fetch("/api/plugins/list");
        const data = await res.json();

        const sig = JSON.stringify(data.plugins);
        if (sig === lastPluginSig) {
            return;
        }
        lastPluginSig = sig;

        installedPlugins.textContent = "";

        if (data.plugins.length === 0) {
            installedPlugins.textContent = "Abhi koi plugin nahi hai.";
            return;
        }

        data.plugins.forEach(function (plugin) {
            const item = document.createElement("div");
            item.className = "plugin-item";

            const info = document.createElement("div");
            info.className = "plugin-info";

            const name = document.createElement("strong");
            name.textContent = plugin.name;

            const size = document.createElement("small");
            size.textContent = plugin.size + " MB";

            info.appendChild(name);
            info.appendChild(size);

            const btn = document.createElement("button");
            btn.className = "btn red";
            btn.textContent = "Remove";
            btn.addEventListener("click", async function () {
                if (!window.confirm("'" + plugin.name + "' plugin delete karna hai?")) return;
                btn.disabled = true; btn.textContent = "Removing...";
                const result = await postJSON("/api/plugins/remove", { name: plugin.name });
                addLine("[Panel] " + result.message);
                btn.disabled = false; btn.textContent = "Remove";
                lastPluginSig = ""; loadInstalled();
            });

            item.appendChild(info);
            item.appendChild(btn);
            installedPlugins.appendChild(item);
        });
    } catch (err) {
        installedPlugins.textContent = "List nahi aayi: " + err;
    }
}

pluginSearchBtn.addEventListener("click", searchPlugins);

pluginSearch.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
        searchPlugins();
    }
});


// ---------- Fabric Mods ----------
async function searchMods() {
    const q = modSearch.value.trim();
    if (!q) return;
    modResults.textContent = "Mods dhoondh raha hoon...";
    try {
        const res = await fetch("/api/mods/search?q=" + encodeURIComponent(q));
        const data = await res.json();
        renderModResults(data);
    } catch (err) { modResults.textContent = "Search fail: " + err; }
}
function renderModResults(data) {
    modResults.textContent = "";
    modHits = data && data.ok ? data.hits : [];
    selectedModIds.clear();
    updateBulkSelection("mod");
    if (!data.ok) { modResults.textContent = data.message; return; }
    if (!data.hits.length) { modResults.textContent = "Koi compatible mod nahi mila."; return; }
    data.hits.forEach(function(hit) {
        const item = document.createElement("div"); item.className = "plugin-item result-item";
        const check = document.createElement("input"); check.type = "checkbox"; check.className = "result-check";
        check.setAttribute("aria-label", hit.title + " select karo");
        check.addEventListener("change", function() {
            if (check.checked) selectedModIds.add(hit.id); else selectedModIds.delete(hit.id);
            updateBulkSelection("mod");
        });
        const info = document.createElement("div"); info.className = "plugin-info";
        const title = document.createElement("strong"); title.textContent = hit.title;
        const desc = document.createElement("span"); desc.textContent = hit.description;
        const meta = document.createElement("small"); meta.textContent = "by " + hit.author + " | " + hit.downloads.toLocaleString() + " downloads | Server: " + hit.server_side + " | Client: " + hit.client_side;
        info.append(title, desc, meta);
        const btn = document.createElement("button"); btn.className = "btn green"; btn.textContent = "Install";
        btn.addEventListener("click", async function() {
            btn.disabled = true; btn.textContent = "Installing...";
            const result = await postJSON("/api/mods/install", {project_id: hit.id});
            addLine("[Panel] " + result.message); btn.textContent = result.ok ? "Installed" : "Install";
            btn.disabled = !!result.ok; loadMods();
        });
        item.append(check, info, btn); modResults.appendChild(item);
    });
}

async function installBulk(kind) {
    const isPlugin = kind === "plugin";
    const selected = Array.from(isPlugin ? selectedPluginIds : selectedModIds).slice(0, 30);
    if (!selected.length) return;
    if (!window.confirm(selected.length + " selected " + (isPlugin ? "plugins" : "mods") + " install karne hain? Server stopped hona chahiye.")) return;
    const button = $(isPlugin ? "pluginBulkInstall" : "modBulkInstall");
    button.disabled = true; button.textContent = "Installing...";
    const result = await postJSON(isPlugin ? "/api/plugins/install-bulk" : "/api/mods/install-bulk", {project_ids: selected});
    addLine("[Panel] " + result.message);
    if (result.results) result.results.forEach(item => addLine("[Bulk] " + item.id + ": " + item.message));
    button.textContent = "Install selected";
    if (isPlugin) { selectedPluginIds.clear(); loadInstalled(); }
    else { selectedModIds.clear(); loadMods(); }
    updateBulkSelection(kind);
}

const pluginSelectAll = $("pluginSelectAll");
if (pluginSelectAll) pluginSelectAll.addEventListener("change", function() {
    const shouldSelect = pluginSelectAll.checked;
    selectedPluginIds.clear();
    if (shouldSelect) pluginHits.forEach(hit => selectedPluginIds.add(hit.id));
    pluginResults.querySelectorAll(".result-check").forEach(check => check.checked = shouldSelect);
    updateBulkSelection("plugin");
});
const modSelectAll = $("modSelectAll");
if (modSelectAll) modSelectAll.addEventListener("change", function() {
    const shouldSelect = modSelectAll.checked;
    selectedModIds.clear();
    if (shouldSelect) modHits.forEach(hit => selectedModIds.add(hit.id));
    modHits.forEach((hit, index) => {
        const check = modResults.querySelectorAll(".result-check")[index];
        if (check) check.checked = shouldSelect;
    });
    updateBulkSelection("mod");
});
if ($("pluginBulkInstall")) $("pluginBulkInstall").addEventListener("click", function() { installBulk("plugin"); });
if ($("modBulkInstall")) $("modBulkInstall").addEventListener("click", function() { installBulk("mod"); });

async function loadMods() {
    if (!installedMods) return;
    try {
        const res = await fetch("/api/mods/list"); const data = await res.json();
        installedMods.textContent = "";
        if (!data.mods || !data.mods.length) { installedMods.textContent = "Abhi koi mod nahi hai (ya selected server mods loader nahi hai)."; return; }
        data.mods.forEach(function(mod) {
            const item = document.createElement("div"); item.className = "plugin-item";
            const info = document.createElement("div"); info.className = "plugin-info";
            const name = document.createElement("strong"); name.textContent = mod.name;
            const size = document.createElement("small"); size.textContent = mod.size + " MB";
            info.append(name, size);
            const btn = document.createElement("button"); btn.className = "btn red"; btn.textContent = "Remove";
            btn.addEventListener("click", async function() {
                if (!window.confirm("'" + mod.name + "' mod delete karna hai?")) return;
                btn.disabled = true; btn.textContent = "Removing...";
                const result = await postJSON("/api/mods/remove", {name: mod.name});
                addLine("[Panel] " + result.message);
                if (!result.ok) { btn.disabled = false; btn.textContent = "Remove"; }
                loadMods();
            });
            item.append(info, btn); installedMods.appendChild(item);
        });
    } catch (err) { installedMods.textContent = "Mod list nahi aayi: " + err; }
}
if (modSearchBtn) modSearchBtn.addEventListener("click", searchMods);
if (modSearch) modSearch.addEventListener("keydown", function(e) { if (e.key === "Enter") searchMods(); });

// ---------- Shuru ----------
async function startPanel() {
    addLine("[Panel] RamFreeMCHosting ready hai. Server chuno ya naya banao.");
    await loadVersions();
    await loadServers(true);
    fetchStatus();
    fetchLogs();
    setInterval(fetchLogs, 1000);
    setInterval(fetchStatus, 2000);
    setInterval(function () {
        loadServers(false);
    }, 4000);
    setInterval(loadInstalled, 8000);
    setInterval(loadMods, 8000);
}

startPanel();
