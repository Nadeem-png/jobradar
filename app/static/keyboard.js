// Feed keyboard shortcuts: j/k next/prev, s shortlist, i ignore, a apply, Enter open.
(function () {
  let sel = -1;

  function cards() {
    return Array.from(document.querySelectorAll("#job-list article[data-job-card]"));
  }

  function paint(cs) {
    cs.forEach((c, idx) => c.classList.toggle("kb-selected", idx === sel));
  }

  function select(i) {
    const cs = cards();
    if (!cs.length) { sel = -1; return; }
    sel = Math.max(0, Math.min(cs.length - 1, i));
    paint(cs);
    cs[sel].scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function act(name) {
    const cs = cards();
    if (sel < 0 || sel >= cs.length) return;
    const el = cs[sel].querySelector('[data-act="' + name + '"]');
    if (el) el.click();
  }

  function typing(t) {
    return (
      t &&
      (t.tagName === "INPUT" ||
        t.tagName === "TEXTAREA" ||
        t.tagName === "SELECT" ||
        t.isContentEditable)
    );
  }

  document.addEventListener("keydown", function (e) {
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!document.getElementById("job-list")) return; // only on the feed
    switch (e.key) {
      case "j": select(sel + 1); e.preventDefault(); break;
      case "k": select(sel < 0 ? 0 : sel - 1); e.preventDefault(); break;
      case "s": act("shortlist"); e.preventDefault(); break;
      case "i": act("ignore"); e.preventDefault(); break;
      case "a": act("apply"); e.preventDefault(); break;
      case "Enter": act("open"); e.preventDefault(); break;
    }
  });

  // After any HTMX swap (list refresh OR a single card being shortlisted/ignored/
  // applied), re-clamp the selection to a valid index and repaint the highlight.
  // Card-level swaps target #job-{id}, not #job-list, so we can't gate on target.
  document.body.addEventListener("htmx:afterSwap", function () {
    const cs = cards();
    if (sel >= cs.length) sel = cs.length - 1; // e.g. the last card was ignored
    paint(cs);
  });
})();
