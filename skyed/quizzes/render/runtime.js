(function () {
  function $(sel) { return document.querySelector(sel); }
  function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

  function shuffle(a) {
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  async function fetchJson(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("Failed to load " + url);
    return await r.json();
  }

  async function fetchText(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("Failed to load " + url);
    return await r.text();
  }

  function setScore(scoreNow, scoreMax) {
    $("#scoreNow").textContent = String(scoreNow);
    $("#scoreMax").textContent = String(scoreMax);
  }

  function setIndex(idx, max) {
    $("#idxNow").textContent = String(idx + 1);
    $("#idxMax").textContent = String(max);
  }

  function safe(s) { return (s == null) ? "" : String(s); }

  // --- renderers ---
  function renderMCQ(host, q, onAnswered) {
    host.innerHTML = q.__html;
    const choicesBox = $("#choices");
    const resultBox = $("#resultBox");
    const hintBox = $("#hintBox");
    const btnHint = $("#btnHint");

    hintBox.textContent = "";
    resultBox.textContent = "";

    (q.choices || []).forEach((c) => {
      const b = el("button", "choice");
      b.textContent = c;
      b.onclick = () => {
        const ok = c === q.answer;
        resultBox.textContent = ok ? "✅ Correct" : "❌ Try again";
        onAnswered(ok);
      };
      choicesBox.appendChild(b);
    });

    btnHint.onclick = () => {
      hintBox.textContent = "Look at the sentence structure. Prepositions show position.";
    };
  }

  function renderQA(host, q, onAnswered) {
    host.innerHTML = q.__html;
    const choicesBox = $("#choices");
    const resultBox = $("#resultBox");
    const hintBox = $("#hintBox");
    const btnHint = $("#btnHint");

    hintBox.textContent = "";
    resultBox.textContent = "";

    (q.choices || []).forEach((c) => {
      const b = el("button", "choice");
      b.textContent = c;
      b.onclick = () => {
        const ok = c === q.answer;
        resultBox.textContent = ok ? "✅ Correct" : "❌ Not the best answer";
        onAnswered(ok);
      };
      choicesBox.appendChild(b);
    });

    btnHint.onclick = () => {
      hintBox.textContent = "Answer must match the question intent (location).";
    };
  }

  async function renderDragDrop(host, q, onAnswered) {
    host.innerHTML = q.__html;

    const resultBox = $("#resultBox");
    const hintBox = $("#hintBox");
    const btnHint = $("#btnHint");
    const zonesHost = $("#zones");
    const sprite = $("#sprite");
    const choiceRow = $("#choiceRow");

    resultBox.textContent = "";
    hintBox.textContent = "";

    // choices pills (optional)
    let selectedPrep = q.correct_prep;
    if (q.ui && q.ui.show_choices && Array.isArray(q.choices)) {
      q.choices.forEach((p) => {
        const pill = el("button", "pill");
        pill.textContent = p;
        pill.onclick = () => {
          selectedPrep = p;
          [...choiceRow.querySelectorAll(".pill")].forEach(x => x.classList.remove("active"));
          pill.classList.add("active");
        };
        choiceRow.appendChild(pill);
      });
      // auto select first
      const first = choiceRow.querySelector(".pill");
      if (first) first.classList.add("active");
      selectedPrep = first ? first.textContent : q.correct_prep;
    }

    // load zones meta (already embedded in q.__zones)
    const zones = q.__zones || [];
    zones.forEach((z) => {
      const dz = el("div", "zone");
      dz.dataset.zoneId = z.id;
      dz.style.left = z.x + "%";
      dz.style.top = z.y + "%";
      dz.style.width = z.w + "%";
      dz.style.height = z.h + "%";
      dz.title = z.label || z.id;

      dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
      dz.ondragleave = () => dz.classList.remove("over");
      dz.ondrop = (e) => {
        e.preventDefault();
        dz.classList.remove("over");

        const okZone = dz.dataset.zoneId === q.zone_id;
        const okPrep = selectedPrep === q.correct_prep;
        const ok = okZone && okPrep;

        resultBox.textContent = ok ? "✅ Correct placement" : "❌ Wrong (zone or preposition)";
        onAnswered(ok);
      };

      zonesHost.appendChild(dz);
    });

    // sprite drag behavior
    sprite.ondragstart = (e) => {
      e.dataTransfer.setData("text/plain", q.object_id || "obj");
    };

    btnHint.onclick = () => {
      hintBox.textContent = `Correct preposition: "${q.correct_prep}". Look for the right target area.`;
    };
  }

  // --- boot ---
  window.SKYED_BOOT = async function ({ jsonUrl }) {
    const pkg = await fetchJson(jsonUrl);
    const host = $("#quizHost");

    const quizzes = pkg.quizzes || [];
    let idx = 0;
    let score = 0;
    const answered = new Set();

    // load templates per type
    const templates = {
      "prepositions_dragdrop": await fetchText("templates/dragdrop.html"),
      "mcq_text": await fetchText("templates/mcq.html"),
      "qa_dialog": await fetchText("templates/qa.html")
    };

    // scene zones file is copied to output/assets/scenes/<scene>/zones.json
    const sceneZones = await fetchJson(`assets/scenes/${pkg.scene}/zones.json`);

    function applyTemplate(tpl, q) {
      return tpl
        .replaceAll("{{PROMPT}}", safe(q.prompt))
        .replaceAll("{{PROMPT_ZH}}", safe(q.prompt_zh))
        .replaceAll("{{SCENE_BG}}", `assets/scenes/${pkg.scene}/${sceneZones.background}`)
        .replaceAll("{{SPRITE_SRC}}", `assets/scenes/${pkg.scene}/sprites/${safe(q.object_id)}.${sceneZones.sprite_ext}`);
    }

    function getZoneRect(zoneId) {
      const z = (sceneZones.zones || []).find(x => x.id === zoneId);
      if (!z) return null;
      return z;
    }

    async function show(i) {
      idx = Math.max(0, Math.min(i, quizzes.length - 1));
      setIndex(idx, quizzes.length);
      setScore(score, quizzes.length);

      const q = quizzes[idx];
      const tpl = templates[q.type];
      if (!tpl) throw new Error("No template for " + q.type);

      q.__html = applyTemplate(tpl, q);

      // embed zones array for dragdrop
      if (q.type === "prepositions_dragdrop") {
        // highlight all zones (invisible by default; CSS handles)
        q.__zones = (sceneZones.zones || []).map(z => ({
          id: z.id, x: z.x, y: z.y, w: z.w, h: z.h, label: z.label
        }));
        await renderDragDrop(host, q, (ok) => onAnswered(q.id, ok));
      } else if (q.type === "mcq_text") {
        renderMCQ(host, q, (ok) => onAnswered(q.id, ok));
      } else if (q.type === "qa_dialog") {
        renderQA(host, q, (ok) => onAnswered(q.id, ok));
      }
    }

    function onAnswered(qid, ok) {
      // score first correct answer only
      if (ok && !answered.has(qid)) {
        answered.add(qid);
        score += 1;
        setScore(score, quizzes.length);
      }
    }

    $("#btnPrev").onclick = () => show(idx - 1);
    $("#btnNext").onclick = () => show(idx + 1);

    // init
    setIndex(0, quizzes.length);
    setScore(0, quizzes.length);

    // create local templates folder mapping (runtime expects /templates/*)
    // (In static output we copy render templates into output/templates/)
    await show(0);
  };
})();
