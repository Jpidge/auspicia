(function () {
  "use strict";
  var COLORS = { QB: "var(--qb)", RB: "var(--rb)", WR: "var(--wr)", TE: "var(--te)", K: "var(--k)", DST: "var(--def)", DEF: "var(--def)" };
  var SLOT = { QB: "QB", RB: "RB", WR: "WR", TE: "TE", FLEX: "W/R/T", K: "K", DST: "DEF" };
  var VERDICT = { start: "Start", monitor: "Monitor", sit: "Sit" };
  var state = { data: null, view: "team", teamId: null, waiver: "ALL", sortKey: "projected_total", sortDir: -1 };

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmt(value) {
    if (value == null || Number.isNaN(Number(value))) return "–";
    return Number(value).toFixed(1);
  }
  function rangeText(player) {
    if (player.q10 == null || player.q90 == null) return "";
    return fmt(player.q10) + "–" + fmt(player.q90);
  }
  function posColor(position) { return COLORS[position] || "var(--muted)"; }
  function knownTeam(id) {
    var teams = state.data.teams;
    for (var i = 0; i < teams.length; i += 1) if (teams[i].team_id === id) return teams[i];
    return null;
  }
  function teamById(id) {
    return knownTeam(id) || knownTeam(state.data.default_team_id) || state.data.teams[0];
  }
  function noteMissing(asked) {
    var note = $("team-note");
    var found = knownTeam(asked);
    if (asked == null || found) {
      state.missingTeam = null;
      note.hidden = true;
      note.textContent = "";
      return;
    }
    state.missingTeam = asked;
    note.hidden = false;
    note.textContent = "Team " + asked + " not found, showing " + teamById(asked).name;
  }

  function row(player) {
    var color = posColor(player.position);
    var slot = SLOT[player.slot] || SLOT[player.position] || player.position;
    var showPos = player.slot && SLOT[player.slot] !== SLOT[player.position];
    var injury = player.injury ? '<span class="inj' + (player.injury === "Q" ? " q" : "") + '">' + esc(player.injury) + "</span>" : "";
    var quiet = player.force_zero || player.bye || player.injury === "IR" || player.injury === "OUT";
    var delta = player.yahoo_delta;
    var deltaClass = delta > 0.2 ? "d-up" : delta < -0.2 ? "d-dn" : "d-eq";
    var deltaHtml = player.yahoo == null ? "" : '<span class="chip ' + deltaClass + '">' + (delta > 0 ? "+" : delta < 0 ? "−" : "±") + Math.abs(delta).toFixed(1) + '<span class="dlabel"> vs Y!</span></span>';
    var yahooHtml = player.yahoo == null ? "" : '<span class="yh">Y! <b class="num">' + fmt(player.yahoo) + "</b></span>" + deltaHtml;
    var proj = quiet ? '<span class="sub">Out — no projection</span>' : (
      '<span class="rng" aria-hidden="true"><span class="trk"></span><span class="band" style="left:' + pct(player.q10) + "%;width:" + Math.max(0, pct(player.q90) - pct(player.q10)) + '%"></span><span class="med" style="left:' + pct(player.q50) + '%"></span></span>' +
      '<span class="medblock"><span class="mednum num">' + fmt(player.q50) + '</span><span class="rngline num">' + esc(rangeText(player)) + "</span></span>" +
      '<span class="yhm">' + yahooHtml + "</span>"
    );
    var verdict = VERDICT[player.verdict] || "Sit";
    var verdictClass = player.verdict === "start" ? "v-start" : player.verdict === "monitor" ? "v-mon" : "v-sit";
    var where = esc(player.nfl_team || "") + " · " + (player.home ? "vs " : "@ ") + esc(player.opponent || "—");
    if (player.kickoff) where += " · " + esc(player.kickoff);
    return '<div class="row" style="--c:' + color + '">' +
      '<button class="rbtn" type="button" aria-expanded="false">' +
      '<span class="slot"><span class="pos" style="--c:' + color + '">' + esc(slot) + "</span>" + (showPos ? "<small>" + esc(SLOT[player.position] || player.position) + "</small>" : "") + "</span>" +
      '<span class="who"><span class="pn">' + esc(player.name) + injury + '</span><span class="pm">' + where + "</span></span>" +
      '<span class="proj">' + proj + "</span>" +
      '<span class="meta"><span class="yhw">' + (quiet ? "" : yahooHtml) + '</span><span class="chip v ' + verdictClass + '">' + verdict + '</span>' +
      '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></span></button>' +
      '<div class="drawer" role="region" aria-label="Details for ' + esc(player.name) + '"><div class="dgrid">' +
      pointsStat(player) +
      stat("10th–90th", esc(rangeText(player) || "—")) +
      stat("Median", fmt(player.q50)) +
      stat("Mean", fmt(player.mean)) +
      stat("Opp rank", player.opp_rank == null ? "—" : String(player.opp_rank)) +
      stat("Vegas implied", player.vegas_implied == null ? "—" : fmt(player.vegas_implied)) +
      snapStat(player) +
      stat("Practice", esc(player.practice || "—")) +
      "</div>" + notes(player.notes) + "</div></div>";
  }
  function nums(values) {
    if (!values || !values.length) return [];
    var out = [];
    for (var i = 0; i < values.length; i += 1) {
      var number = Number(values[i]);
      if (!Number.isNaN(number)) out.push(number);
    }
    return out;
  }
  function compact(value) {
    var rounded = Math.round(value * 10) / 10;
    return Math.abs(rounded - Math.round(rounded)) < 0.05 ? String(Math.round(rounded)) : rounded.toFixed(1);
  }
  function spark(values, width, height) {
    if (!values || values.length < 2) return "";
    var max = Math.max.apply(null, values);
    var min = Math.min.apply(null, values);
    var span = (max - min) || 1;
    var pts = values.map(function (value, index) {
      return [
        (index / (values.length - 1)) * (width - 8) + 4,
        height - 4 - ((value - min) / span) * (height - 8)
      ];
    });
    var path = pts.map(function (point, index) {
      return (index ? "L" : "M") + point[0].toFixed(1) + " " + point[1].toFixed(1);
    }).join(" ");
    var last = pts[pts.length - 1];
    return '<svg class="spark" viewBox="0 0 ' + width + " " + height + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + path + " L" + last[0].toFixed(1) + " " + height + ' L4 ' + height + 'Z" fill="var(--c)" opacity=".12"/>' +
      '<path d="' + path + '" fill="none" stroke="var(--c)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' +
      '<path d="M' + last[0].toFixed(1) + " " + last[1].toFixed(1) + 'l0 0" stroke="var(--c)" stroke-width="7" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>';
  }
  function pointsStat(player) {
    var values = nums(player.recent_points);
    var body = values.length < 2
      ? '<div class="val num">—</div>'
      : spark(values, 120, 40) + '<div class="sub num">' + values.map(function (value) { return Number(value).toFixed(1); }).join(" · ") + "</div>";
    return '<div class="stat"><div class="k">Last 6 weeks</div>' + body + "</div>";
  }
  function snapSeries(player) {
    if (player.position === "DST" || player.position === "DEF") return [];
    return nums(player.snap_share).filter(function (value) { return value > 0; });
  }
  function snapStat(player) {
    var values = snapSeries(player);
    if (values.length < 2) {
      return '<div class="stat"><div class="k">Snap share</div><div class="val num">—</div></div>';
    }
    var last = values[values.length - 1];
    var delta = last - values[0];
    var mark = delta >= 0 ? "▲" : "▼";
    return '<div class="stat"><div class="k">Snap share</div><div class="val num">' + compact(last) + '%<small> ' + mark + compact(Math.abs(delta)) + "</small></div>" + spark(values, 120, 24) + "</div>";
  }
  function pct(value) {
    var max = 30;
    return Math.max(0, Math.min(100, (Number(value) / max) * 100));
  }
  function stat(label, value) {
    return '<div class="stat"><div class="k">' + esc(label) + '</div><div class="val num">' + value + "</div></div>";
  }
  function notes(items) {
    if (!items || !items.length) return "";
    return '<ul class="why">' + items.map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") + "</ul>";
  }

  function renderTeam() {
    var data = state.data;
    var team = teamById(state.teamId);
    var week = data.week;
    $("league-line").textContent = data.league.name + " · " + data.league.teams + "-team " + String(data.league.format || "").toUpperCase() + " · " + (data.league.ppr ? "PPR" : "Non-PPR");
    $("team-title").textContent = team.name + " · Week " + week.week;
    $("countdown").textContent = week.countdown_label || "";
    $("kickoff-label").textContent = week.kickoff_label || "";
    $("kickoff").hidden = !week.countdown_label && !week.kickoff_label;
    var select = $("team-select");
    select.innerHTML = data.teams.map(function (item) {
      return '<option value="' + item.team_id + '">' + esc(item.name) + "</option>";
    }).join("");
    select.value = String(team.team_id);
    var opp = team.opponent_projected_total;
    var wp = team.win_probability;
    var wpPct = wp == null ? null : Math.round(wp * 100);
    $("matchup").innerHTML = '<div class="mu"><div class="t me"><span class="sub">' + esc(team.name) + " (" + esc(team.record) + ')</span><span class="pts num">' + fmt(team.projected_total) + '</span><span class="sub">Gridiron Lab proj.</span></div><span class="vs">VS</span><div class="t r"><span class="sub">' + esc(team.opponent_name || "No opponent") + '</span><span class="pts num">' + (opp == null ? "–" : fmt(opp)) + '</span><span class="sub">Gridiron Lab proj.</span></div></div>' +
      (wpPct == null ? "" : '<div class="wp"><div class="wpbar" role="img" aria-label="Win probability ' + wpPct + ' percent"><i style="width:' + wpPct + '%"></i></div><div class="wplab"><span>Win probability <b class="num">' + wpPct + '%</b></span><span><b class="num">' + (100 - wpPct) + "%</b></span></div></div>");
    var calls = team.calls || [];
    var tough = $("tough-card");
    tough.classList.toggle("is-empty", !calls.length);
    tough.innerHTML = calls.length ? "<h2>Toughest calls</h2>" + '<div class="tc">' + calls.map(function (call) {
      return '<div class="tci"><div class="q">' + esc(call.slot) + '</div><div class="pair"><div class="p win">' + esc(call.starter_name) + '<small class="num">' + fmt(call.starter_projection) + ' median</small></div><span class="or">OR</span><div class="p" style="text-align:right">' + esc(call.alternative_name) + '<small class="num">' + fmt(call.alternative_projection) + ' median</small></div></div><div class="sub">' + esc(call.starter_name) + " leads by " + fmt(call.margin) + ".</div></div>";
    }).join("") + "</div>" : '<p>Toughest calls: none within ' + data.close_call_margin + " point.</p>";
    var bench = (team.bench || []).concat(team.inactive || []);
    $("lineup-col").innerHTML = '<div class="sec-h"><h2>Recommended lineup</h2><span class="sub">Tap a row for details</span></div><p class="ylegend">Y! = Yahoo projection; chip = ours minus Yahoo</p><div class="list">' + team.starters.map(row).join("") + '</div><div class="sec-h"><h2>Bench</h2><span class="sub">' + data.lineup.bench + " bench · " + data.lineup.ir + ' IR</span></div><div class="list">' + bench.map(row).join("") + "</div>";
    $("legend-card").innerHTML = '<h2>How to read a row</h2><div class="legend"><span><i class="sw"></i>10th–90th pct range</span><span><i class="sw m"></i>Median projection</span><span><span class="chip d-up">+2.1</span> ours minus Yahoo</span></div><p class="sub">The lineup maximizes median points under the league slots. A player on bye, OUT, or IR is not started when someone else can fill the slot.</p>';
  }

  function renderWaivers() {
    var data = state.data;
    $("waiver-sub").textContent = "Week " + data.week.week + " · example suggestions, not a live waiver wire";
    var positions = ["ALL", "QB", "RB", "WR", "TE", "K", "DST"];
    $("pills").innerHTML = positions.map(function (position) {
      var label = position === "DST" ? "DEF" : position === "ALL" ? "All" : position;
      return '<button class="pill" type="button" aria-pressed="' + (position === state.waiver) + '" data-p="' + position + '">' + label + "</button>";
    }).join("");
    var list = data.waivers.filter(function (item) { return state.waiver === "ALL" || item.position === state.waiver; });
    $("wlist").innerHTML = list.map(function (item) {
      var color = posColor(item.position);
      var weeks = item.q50_next || [];
      var total = weeks.reduce(function (sum, value) { return sum + Number(value); }, 0);
      return '<article class="card wc" style="--c:' + color + '"><div class="ad"><div><span class="plus">Add</span>' + esc(item.name) + '</div><div><span class="minus">Drop</span>' + esc(item.drop_name || "—") + (item.drop_position ? " (" + esc(SLOT[item.drop_position] || item.drop_position) + ")" : "") + '</div></div><div class="hd"><span class="pos" style="--c:' + color + '">' + esc(SLOT[item.position] || item.position) + '</span><div class="pm">' + esc(item.nfl_team || "") + " · " + esc(item.note || "") + "</div></div>" +
        '<div class="next3">' + weeks.map(function (value, index) { return "<div><small>WK " + (data.week.week + index) + '</small><b class="num">' + fmt(value) + "</b></div>"; }).join("") + "</div>" +
        '<div class="wm"><span>Next 3<br><b class="num">' + fmt(total) + "</b></span><span>ROS proj<br><b class=\"num\">" + fmt(item.ros) + "</b></span>" + snapTrend(item) + "</div></article>";
    }).join("") || '<p class="sub">No pickups at this position.</p>';
  }
  function snapTrend(item) {
    if (item.position === "DST" || item.position === "DEF") {
      return '<span style="margin-left:auto">Snap trend n/a</span>';
    }
    var values = snapSeries(item);
    if (values.length < 2) return '<span style="margin-left:auto">—</span>';
    var delta = values[values.length - 1] - values[0];
    var color = delta > 0 ? "var(--good)" : delta < 0 ? "var(--bad)" : "var(--muted)";
    var label = delta > 0 ? "▲" + compact(delta) + "%" : delta < 0 ? "▼" + compact(-delta) + "%" : "Steady";
    return '<span>Snaps<br><b class="num" style="color:' + color + '">' + label + "</b></span>" + spark(values, 90, 28);
  }

  function renderLeague() {
    var data = state.data;
    $("league-sub").textContent = data.league.name + " · Week " + data.week.week + " projections";
    var rows = data.standings.slice().sort(function (a, b) {
      var left = a[state.sortKey];
      var right = b[state.sortKey];
      if (typeof left === "string") return left.localeCompare(right) * state.sortDir;
      return (Number(left) - Number(right)) * state.sortDir;
    });
    var mine = state.teamId;
    $("ltbl").querySelector("tbody").innerHTML = rows.map(function (rowItem) {
      var mark = rowItem.team_id === mine ? " mine" : "";
      var you = rowItem.team_id === data.default_team_id ? ' <span class="chip d-eq">You</span>' : "";
      var viewing = rowItem.team_id === state.teamId && rowItem.team_id !== data.default_team_id ? ' <span class="chip d-eq">Viewing</span>' : "";
      return '<tr class="' + mark.trim() + '"><td class="num">' + rowItem.rank + '</td><td class="tn"><div>' + esc(rowItem.name) + you + viewing + '</div><div class="opp-line">vs ' + esc(rowItem.opponent_name || "—") + '</div></td><td class="num hide-s">' + esc(rowItem.record) + '</td><td class="sub hide-s">' + esc(rowItem.opponent_name || "—") + '</td><td class="r num"><b>' + fmt(rowItem.projected_total) + '</b></td><td class="hide-s"><div class="mbar"><i style="width:' + (Number(rowItem.share_of_leader) * 100) + '%"></i></div></td></tr>';
    }).join("");
    var headers = document.querySelectorAll("#ltbl th[data-k]");
    for (var i = 0; i < headers.length; i += 1) {
      var th = headers[i];
      var button = th.querySelector("button");
      var base = button.textContent.replace(/ [▴▾]$/, "");
      if (th.getAttribute("data-k") === state.sortKey) {
        var dir = state.sortDir > 0 ? "ascending" : "descending";
        th.setAttribute("aria-sort", dir);
        button.textContent = base + (state.sortDir > 0 ? " ▴" : " ▾");
      } else {
        th.removeAttribute("aria-sort");
        button.textContent = base;
      }
    }
  }

  function renderLab() {
    var lab = state.data.lab;
    var positions = lab.positions || [];
    var mae = positions.map(function (item) {
      var seasons = item.seasons || [];
      var mean = seasons.reduce(function (sum, season) { return sum + Number(season.mae); }, 0) / (seasons.length || 1);
      return { position: item.position, mae: mean };
    });
    var maxMae = mae.reduce(function (max, item) { return Math.max(max, item.mae); }, 1);
    var coverage = lab.pooled_coverage || {};
    function labText(value) { return esc(value).replace(/DST/g, "DEF"); }
    var notes = (lab.notes || []).map(function (note) { return "<li>" + labText(note) + "</li>"; }).join("");
    var criteria = (lab.criteria || []).map(function (item) {
      return "<li><span>" + labText(item.detail) + '</span><span class="' + (item.pass ? "pass" : "fail") + '">' + (item.pass ? "Pass" : "Fail") + "</span></li>";
    }).join("");
    var bars = (lab.mae_diff_by_season || []).map(function (item) {
      var gain = -Number(item.mae_diff);
      var label = (gain > 0 ? "+" : "") + gain.toFixed(3);
      return '<div class="l"><span class="num">' + item.season + '</span><span class="b"><i style="width:' + Math.min(100, Math.max(0, gain) / 0.6 * 100) + '%;background:var(--accent)"></i></span><b class="num">' + label + "</b></div>";
    }).join("");
    $("lab-root").innerHTML = '<div class="note" role="note">' + esc((lab.notes && lab.notes[0]) || "Backtest") + " Promoted: " + (lab.promoted ? "yes" : "no") + ".</div>" +
      '<div class="lab"><div class="card"><h2>MAE by position</h2><p class="sub">Mean of seasonal q50 MAE, points per game. Lower is better. Compared with the legacy baseline, not Yahoo.</p><div class="mae">' +
      mae.map(function (item) {
        return '<div class="l"><span class="pos" style="--c:' + posColor(item.position) + '">' + esc(SLOT[item.position] || item.position) + '</span><span class="b"><i style="--c:' + posColor(item.position) + ";width:" + (item.mae / maxMae * 100) + '%"></i></span><b class="num">' + item.mae.toFixed(2) + "</b></div>";
      }).join("") + '</div></div><div class="card"><h2>Interval coverage</h2><p class="sub">Share of outcomes inside the calibrated 10–90 range. Target band is 0.77 to 0.83.</p><div class="mae">' +
      Object.keys(coverage).map(function (position) {
        var value = coverage[position];
        var inside = value >= 0.77 && value <= 0.83;
        return '<div class="l cov"><span class="pos" style="--c:' + posColor(position) + '">' + esc(SLOT[position] || position) + '</span><span class="b"><i style="--c:' + posColor(position) + ";width:" + (value * 100) + '%"></i><span class="band"></span></span><span class="num">' + Number(value).toFixed(3) + '</span><b class="' + (inside ? "pass" : "fail") + '">' + (inside ? "In band" : "Outside") + "</b></div>";
      }).join("") + '</div></div><div class="card"><h2>Promotion rule</h2><ul class="crit">' + criteria + '</ul></div><div class="card"><h2>Improvement over baseline</h2><p class="sub">Baseline q50 MAE minus the model. Positive means the model was closer that season.</p><div class="mae">' + bars + '</div><p class="sub">Lineup points versus that baseline: +' + Number(lab.points_gained_vs_baseline).toFixed(1) + ' over the backtest. Not a Yahoo result.</p></div><div class="card wide"><h2>Notes</h2><ul class="why">' + notes + "</ul></div></div>";
  }

  window.addEventListener("hashchange", function () {
    if (!state.data) return;
    var hash = (location.hash || "").replace(/^#/, "");
    var teamMatch = /^team=(\d+)$/.exec(hash);
    if (teamMatch) {
      var asked = Number(teamMatch[1]);
      var found = knownTeam(asked);
      noteMissing(found ? null : asked);
      state.teamId = teamById(asked).team_id;
      if (found) {
        try { localStorage.setItem("gl-team", String(state.teamId)); } catch (error) { /* private mode */ }
      }
      renderTeam();
      renderLeague();
      show("team", false);
      return;
    }
    if (hash === "waivers" || hash === "league" || hash === "lab") show(hash, false);
  });

  function show(view, focus) {
    state.view = view;
    var tabs = document.querySelectorAll("[role=tab]");
    for (var i = 0; i < tabs.length; i += 1) {
      var tab = tabs[i];
      var on = tab.getAttribute("data-v") === view;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      var panel = $("v-" + tab.getAttribute("data-v"));
      panel.classList.toggle("on", on);
      panel.hidden = !on;
      if (on && focus) tab.focus();
    }
    var hash = view === "team" ? "#team=" + (state.missingTeam != null ? state.missingTeam : state.teamId) : "#" + view;
    try { history.replaceState(null, "", hash); } catch (error) { /* file URLs can reject this */ }
    window.scrollTo(0, 0);
  }

  function boot(data) {
    state.data = data;
    document.title = "Gridiron Lab · " + data.league.name;
    var badge = data.example;
    $("example-badge").hidden = !badge;
    $("footer-badge").hidden = !badge;
    $("footer-text").textContent = data.disclaimer || "";
    $("status").hidden = true;
    var hash = (location.hash || "").replace(/^#/, "");
    var teamMatch = /^team=(\d+)$/.exec(hash);
    var stored = null;
    try { stored = localStorage.getItem("gl-team"); } catch (error) { stored = null; }
    if (teamMatch) {
      var asked = Number(teamMatch[1]);
      var found = knownTeam(asked);
      noteMissing(found ? null : asked);
      state.teamId = teamById(asked).team_id;
      if (found) {
        try { localStorage.setItem("gl-team", String(state.teamId)); } catch (error) { /* private mode */ }
      }
    } else {
      var fromStore = stored != null && stored !== "" ? Number(stored) : data.default_team_id;
      noteMissing(null);
      state.teamId = teamById(knownTeam(fromStore) ? fromStore : data.default_team_id).team_id;
      try { localStorage.setItem("gl-team", String(state.teamId)); } catch (error) { /* private mode */ }
    }
    if (hash === "waivers" || hash === "league" || hash === "lab") state.view = hash;
    renderTeam();
    renderWaivers();
    renderLeague();
    renderLab();
    show(state.view, false);
    $("v-" + state.view).hidden = false;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".rbtn");
    if (!button) return;
    var open = !button.parentNode.classList.contains("open");
    button.parentNode.classList.toggle("open", open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
  });
  $("team-select").addEventListener("change", function () {
    noteMissing(null);
    state.teamId = Number($("team-select").value);
    try { localStorage.setItem("gl-team", String(state.teamId)); } catch (error) { /* ignore */ }
    renderTeam();
    renderLeague();
    if (state.view === "team") show("team", false);
  });
  $("pills").addEventListener("click", function (event) {
    var pill = event.target.closest(".pill");
    if (!pill) return;
    state.waiver = pill.getAttribute("data-p");
    renderWaivers();
  });
  $("ltbl").querySelector("thead").addEventListener("click", function (event) {
    var th = event.target.closest("th[data-k]");
    if (!th) return;
    var key = th.getAttribute("data-k");
    if (key === state.sortKey) state.sortDir = -state.sortDir;
    else {
      state.sortKey = key;
      state.sortDir = key === "name" || key === "opponent_name" ? 1 : -1;
    }
    renderLeague();
  });
  var tabs = [].slice.call(document.querySelectorAll("[role=tab]"));
  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () { show(tab.getAttribute("data-v"), false); });
    tab.addEventListener("keydown", function (event) {
      var next = event.key === "ArrowRight" ? index + 1 : event.key === "ArrowLeft" ? index - 1 : null;
      if (next === null) return;
      event.preventDefault();
      show(tabs[(next + tabs.length) % tabs.length].getAttribute("data-v"), true);
    });
  });
  var THEME_MODES = ["rowan", "aqua", "midnight", "carbon", "light", "dark"];
  var themeSelect = $("theme-select");
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    themeSelect.value = mode;
  }
  themeSelect.addEventListener("change", function () {
    applyTheme(themeSelect.value);
    try { localStorage.setItem("gl-theme", themeSelect.value); } catch (error) { /* ignore */ }
  });
  var savedTheme = null;
  try { savedTheme = localStorage.getItem("gl-theme"); } catch (error) { savedTheme = null; }
  applyTheme(THEME_MODES.indexOf(savedTheme) !== -1 ? savedTheme : "dark");

  fetch("dashboard.json")
    .then(function (response) {
      if (!response.ok) throw new Error("dashboard.json " + response.status);
      return response.json();
    })
    .then(boot)
    .catch(function () {
      $("status").textContent = "Could not load dashboard.json. Serve this folder over HTTP, for example: python -m http.server -d site";
    });
})();
