// JobRadar client-side glue: drawer, toast, filters, kanban drag-drop.

function openDrawer() {
  var d = document.getElementById("drawer");
  var b = document.getElementById("drawer-backdrop");
  if (d) d.classList.remove("translate-x-full");
  if (b) b.classList.remove("hidden");
}

function closeDrawer() {
  var d = document.getElementById("drawer");
  var b = document.getElementById("drawer-backdrop");
  if (d) d.classList.add("translate-x-full");
  if (b) b.classList.add("hidden");
}

var _toastTimer = null;
function showToast(message) {
  var t = document.getElementById("toast");
  if (!t) return;
  t.textContent = message;
  t.classList.remove("hidden");
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function () {
    t.classList.add("hidden");
  }, 4000);
}
window.showToast = showToast;

function resetFilters() {
  var f = document.getElementById("filters");
  if (!f) return;
  f.reset();
  var lbl = document.getElementById("min-score-val");
  if (lbl) lbl.textContent = "0";
  var yrs = document.getElementById("max-years-val");
  if (yrs) yrs.textContent = "Any";
  // Trigger a refresh with defaults.
  if (window.htmx) {
    htmx.ajax("GET", "/jobs-partial", { target: "#job-list", swap: "innerHTML", source: f });
  }
}

// Toast triggered from the server via the HX-Trigger response header.
document.body.addEventListener("toast", function (e) {
  if (e.detail && e.detail.message) showToast(e.detail.message);
});

// Server can ask the drawer to close (e.g. after adding a job).
document.body.addEventListener("closeDrawer", function () {
  closeDrawer();
});

// Server can ask the feed list to re-render (e.g. after ignoring a job), so the
// count/pagination header stays accurate. Respects the current sidebar filters.
document.body.addEventListener("refreshFeed", function () {
  var f = document.getElementById("filters");
  if (f && window.htmx) {
    htmx.ajax("GET", "/jobs-partial", { target: "#job-list", swap: "innerHTML", source: f });
  }
});

// Close drawer with Escape.
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") closeDrawer();
});

// --- Kanban drag-drop (SortableJS) -------------------------------------- //
function initKanban() {
  if (!window.Sortable) return;
  document.querySelectorAll(".kanban-list").forEach(function (list) {
    if (list._sortable) return; // avoid double-init after HTMX swaps
    list._sortable = Sortable.create(list, {
      group: "kanban",
      animation: 150,
      ghostClass: "kanban-ghost",
      onEnd: function (evt) {
        var card = evt.item;
        var jobId = card.getAttribute("data-job-id");
        var newStatus = evt.to.getAttribute("data-status");
        if (!jobId || !newStatus) return;
        htmx.ajax("POST", "/jobs/" + jobId + "/move", {
          target: "#tracker-board",
          swap: "outerHTML",
          values: { status: newStatus },
        });
      },
    });
  });
}
window.initKanban = initKanban;

document.addEventListener("DOMContentLoaded", function () {
  initKanban();
});
