import asyncio, json, os, re, subprocess, sys, struct, time, zlib
PRE_NAMES = []
"""Live suite: one or more real steps per illustrator_* tool, with independent read-back.

See live_harness.py for how it talks to Illustrator and its safety rules."""
from live_harness import *

DOC = "mcp-live-test"
IDS = {}


def probe(js):
    """Independent read-back straight through osascript (not through any MCP tool)."""
    f = WORK / "probe.jsx"
    f.write_text("(function(){ var ui = app.userInteractionLevel; app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS; try { var v = (function(){ %s })(); return JSON.stringify(v); } catch(e) { return JSON.stringify({probe_error: String(e)}); } finally { app.userInteractionLevel = ui; } })()" % js, encoding="utf-8")
    r = subprocess.run(["osascript", "-e", f'tell application id "com.adobe.illustrator" to do javascript (POSIX file "{f}")'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout.strip())


def make_png(path, w=64, h=48):
    rows = []
    for y in range(h):
        row = b"\x00"
        for x in range(w):
            row += bytes([(x * 4) % 256, (y * 5) % 256, 120])
        rows.append(row)
    raw = b"".join(rows)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    pathlib.Path(path).write_bytes(png)


async def main():
    only = set(sys.argv[1:])
    info = probe("var o = []; for (var i = 0; i < app.documents.length; i++) o.push([app.documents[i].name, app.documents[i].pageItems.length]); return o;")
    mine = lambda n: n.startswith("mcp-live") or n == "t1.ai"
    # A blank, untouched "Untitled-N" (Illustrator opens one on launch) is left alone and ignored;
    # anything else that this harness did not create stops the run.
    global PRE_NAMES
    PRE_NAMES = [n for n, items in info if re.fullmatch(r"Untitled-\d+", n) and items == 0]
    foreign = [n for n, _ in info if not mine(n) and n not in PRE_NAMES]
    if foreign:
        print('ABORT: documents not created by this harness are open:', foreign); return
    for n, _ in info:
        if mine(n):
            probe("for (var i = 0; i < app.documents.length; i++) if (app.documents[i].name == %s) { app.documents[i].activate(); app.documents[i].close(SaveOptions.DONOTSAVECHANGES); break; } return 1;" % json.dumps(n))

    # ---------------- illustrator_document ----------------
    async def doc_list_empty():
        env, _ = await T("illustrator_document", action="list")
        return ok(env) and env["result"]["count"] == len(PRE_NAMES) or f"unexpected: {env}"
    await step("illustrator_document", "list (only pre-existing blank documents open)", doc_list_empty)

    async def doc_create():
        env, _ = await T("illustrator_document", action="create", width=600, height=400, name=DOC, color_mode="RGB")
        return (ok(env) and env["result"]["name"] == DOC and env["result"]["width"] == 600) or env
    await step("illustrator_document", "create 600x400 RGB", doc_create)

    async def doc_list_one():
        env, _ = await T("illustrator_document", action="list")
        names = [d.get("name") for d in env["result"]["documents"]]
        return (ok(env) and DOC in names) or env
    await step("illustrator_document", "list shows the new document", doc_list_one)

    # ---------------- illustrator_execute_script ----------------
    DRAW = """
    var doc = app.activeDocument;
    var r = doc.pathItems.rectangle(-50, 50, 120, 80); r.name = 'rect_a'; r.stroked = false;
    var c = new RGBColor(); c.red = 200; c.green = 30; c.blue = 30; r.fillColor = c;
    var e = doc.pathItems.ellipse(-60, 250, 90, 90); e.name = 'ell_b'; e.stroked = false;
    var c2 = new RGBColor(); c2.red = 30; c2.green = 90; c2.blue = 200; e.fillColor = c2;
    var t = doc.textFrames.add(); t.contents = 'Hello big world, hello'; t.position = [50, -250]; t.name = 'txt_c';
    t.textRange.characterAttributes.size = 18;
    'drawn ' + doc.pageItems.length;
    """
    async def es_draw():
        env, _ = await T("illustrator_execute_script", script=DRAW, return_preview=False, description="live test: draw")
        d = res_of(env)
        return (ok(env) and "drawn 3" in str(d)) or env
    await step("illustrator_execute_script", "draw rect, ellipse, text; returns value", es_draw)

    async def es_readback():
        v = probe("var d = app.activeDocument; return {n: d.pageItems.length, names: [d.pageItems[0].name, d.pageItems[1].name, d.pageItems[2].name]};")
        return v["n"] == 3 or v
    await step("illustrator_execute_script", "independent read-back: 3 items in document", es_readback)

    async def es_syntax_error():
        env, _ = await T("illustrator_execute_script", script="var x = ;", return_preview=False)
        err = env.get("error") or {}
        return (env.get("ok") is False and bool(err.get("code"))) or env
    await step("illustrator_execute_script", "syntax error -> structured error code", es_syntax_error)

    async def es_runtime_error():
        env, _ = await T("illustrator_execute_script", script="app.activeDocument.noSuchThing.x = 1;", return_preview=False)
        return (env.get("ok") is False and bool((env.get("error") or {}).get("code"))) or env
    await step("illustrator_execute_script", "runtime error -> structured error code", es_runtime_error)

    async def es_preview():
        env, imgs = await T("illustrator_execute_script", script="1+1", return_preview=True, preview_mode="artboard")
        return (ok(env) and len(imgs) >= 1) or f"ok={env.get('ok')} images={len(imgs)} diag={str(env.get('diagnostics'))[:200]}"
    await step("illustrator_execute_script", "return_preview returns an image", es_preview)

    # ---------------- illustrator_get_document ----------------
    async def gd_doc():
        env, _ = await T("illustrator_get_document", scope="document")
        r = res_of(env)
        s = json.dumps(r)
        return (ok(env) and "rect_a" in s and "txt_c" in s and DOC in s) or env
    await step("illustrator_get_document", "scope=document lists named items", gd_doc)

    async def gd_app():
        env, _ = await T("illustrator_get_document", scope="app")
        return (ok(env) and "30.8" in json.dumps(res_of(env))) or env
    await step("illustrator_get_document", "scope=app reports Illustrator 30.8", gd_app)

    async def gd_both():
        env, _ = await T("illustrator_get_document", scope="both")
        r = res_of(env)
        return (ok(env) and isinstance(r, dict) and "document" in r and "app" in r) or env
    await step("illustrator_get_document", "scope=both has document and app", gd_both)

    async def gd_paging():
        env, _ = await T("illustrator_get_document", scope="document", max_items=1)
        s = json.dumps(res_of(env))
        return (ok(env) and ("truncated" in s or "nextOffset" in s)) or env
    await step("illustrator_get_document", "max_items=1 reports truncation", gd_paging)

    # ---------------- illustrator_query_items ----------------
    async def q_all():
        env, _ = await T("illustrator_query_items", targets={"type": "all", "recursive": True})
        s = json.dumps(res_of(env))
        return (ok(env) and "rect_a" in s and "ell_b" in s and "txt_c" in s) or env
    await step("illustrator_query_items", "type=all finds the 3 items", q_all)

    async def q_query():
        env, _ = await T("illustrator_query_items", targets={"type": "query", "itemType": "PathItem", "pattern": "rect_*"})
        s = json.dumps(res_of(env))
        return (ok(env) and "rect_a" in s and "ell_b" not in s) or env
    await step("illustrator_query_items", "type=query itemType+pattern filters", q_query)

    async def q_layer():
        env, _ = await T("illustrator_query_items", targets={"type": "layer", "layer": "Layer 1"})
        return (ok(env) and "txt_c" in json.dumps(res_of(env))) or env
    await step("illustrator_query_items", "type=layer lists Layer 1", q_layer)

    async def q_selection():
        probe("app.activeDocument.selection = null; return 1;")
        env, _ = await T("illustrator_query_items", targets={"type": "selection"})
        return ok(env) or env
    await step("illustrator_query_items", "type=selection with nothing selected", q_selection)

    # ---------------- illustrator_inspect ----------------
    async def insp_structure():
        env, _ = await T("illustrator_inspect", view="structure", max_depth=2)
        nodes = json.dumps(res_of(env))
        for it in (res_of(env) or {}).get("layers", []) if isinstance(res_of(env), dict) else []:
            pass
        return (ok(env) and "rect_a" in nodes) or env
    await step("illustrator_inspect", "view=structure", insp_structure)

    async def insp_artboard():
        env, _ = await T("illustrator_inspect", view="artboard", artboard_index=0)
        r = res_of(env)
        # collect uuids by name
        def walk(n):
            if isinstance(n, dict):
                if n.get("name") in ("rect_a", "ell_b", "txt_c") and n.get("uuid"):
                    IDS[n["name"]] = str(n["uuid"])
                    IDS["document"] = (r.get("document") or {}).get("name") if isinstance(r, dict) else DOC
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(r)
        return (ok(env) and len(IDS) >= 4) or f"uuids={IDS} env={str(env)[:300]}"
    await step("illustrator_inspect", "view=artboard returns uuids for all items", insp_artboard)

    async def insp_details():
        env, _ = await T("illustrator_inspect", view="details", uuids=[IDS["rect_a"], IDS["txt_c"]])
        s = json.dumps(res_of(env))
        return (ok(env) and "#c81e1e" in s.lower() and "Hello big world" in s) or f"{s[:400]}"
    await step("illustrator_inspect", "view=details reads exact fill and text", insp_details)

    async def insp_selection():
        probe("var d = app.activeDocument; d.selection = [d.pageItems.getByName('rect_a')]; return 1;")
        env, _ = await T("illustrator_inspect", view="selection")
        return (ok(env) and "rect_a" in json.dumps(res_of(env))) or env
    await step("illustrator_inspect", "view=selection sees the selected item", insp_selection)

    async def insp_unknown():
        env, _ = await T("illustrator_inspect", view="details", uuids=["999999"])
        s = json.dumps(env)
        return ("failed_objects" in s or env.get("ok") is False) or env
    await step("illustrator_inspect", "unknown uuid is reported, not a crash", insp_unknown)

    # ---------------- illustrator_artboards ----------------
    async def ab_list():
        env, _ = await T("illustrator_artboards", action="list")
        return (ok(env) and "600" in json.dumps(res_of(env))) or env
    await step("illustrator_artboards", "list", ab_list)

    async def ab_presets():
        env, _ = await T("illustrator_artboards", action="presets")
        return (ok(env) and "A4" in json.dumps(res_of(env))) or env
    await step("illustrator_artboards", "presets include A4", ab_presets)

    async def ab_create():
        env, _ = await T("illustrator_artboards", action="create", preset="A4", name="second")
        n = probe("return app.activeDocument.artboards.length;")
        return (ok(env) and n == 2) or f"n={n} env={str(env)[:300]}"
    await step("illustrator_artboards", "create A4 'second' (2 artboards)", ab_create)

    async def ab_update():
        env, _ = await T("illustrator_artboards", action="update", artboard_name="second", new_name="second-v2")
        names = probe("var a = app.activeDocument.artboards, o = []; for (var i = 0; i < a.length; i++) o.push(a[i].name); return o;")
        return (ok(env) and "second-v2" in names) or f"{names} {str(env)[:200]}"
    await step("illustrator_artboards", "update: rename", ab_update)

    async def ab_fit():
        env, _ = await T("illustrator_artboards", action="fit", artboard_index=0, scope="all", padding=10)
        return ok(env) or env
    await step("illustrator_artboards", "fit to artwork with padding", ab_fit)

    async def ab_activate():
        env, _ = await T("illustrator_artboards", action="activate", artboard_index=1)
        i = probe("return app.activeDocument.artboards.getActiveArtboardIndex();")
        return (ok(env) and i == 1) or f"active={i} {str(env)[:200]}"
    await step("illustrator_artboards", "activate index 1 (read back)", ab_activate)

    async def ab_delete():
        env, _ = await T("illustrator_artboards", action="delete", artboard_name="second-v2")
        n = probe("return app.activeDocument.artboards.length;")
        return (ok(env) and n == 1) or f"n={n} {str(env)[:200]}"
    await step("illustrator_artboards", "delete 'second-v2' (back to 1)", ab_delete)

    async def ab_delete_last():
        env, _ = await T("illustrator_artboards", action="delete", artboard_index=0)
        n = probe("return app.activeDocument.artboards.length;")
        return (env.get("ok") is False and n == 1) or f"n={n} {str(env)[:200]}"
    await step("illustrator_artboards", "refuses to delete the last artboard", ab_delete_last)
    # fit moved artboard 0; restore a known artboard for later steps
    probe("var a = app.activeDocument.artboards[0]; a.artboardRect = [0, 0, 600, -400]; return 1;")

    # ---------------- illustrator_execute_task ----------------
    async def et_create():
        env, _ = await T("illustrator_execute_task", payload={"task": "element_create", "params": {
            "id": "T1", "type": "rect", "x": 400, "y": 50, "width": 60, "height": 40, "fill": {"r": 10, "g": 160, "b": 80}}},
            return_preview=False)
        v = probe("var d = app.activeDocument, n = 0; for (var i = 0; i < d.pageItems.length; i++) { if (d.pageItems[i].note.indexOf('T1') >= 0 || d.pageItems[i].name == 'T1') n++; } return {n: n, total: d.pageItems.length};")
        return (ok(env) and v["total"] == 4) or f"{v} {str(env)[:300]}"
    await step("illustrator_execute_task", "element_create rect (id T1)", et_create)

    async def et_batch():
        env, _ = await T("illustrator_execute_task", payload={"task": "element_create_batch", "params": {
            "template": {"type": "ellipse", "rx": 4, "ry": 3, "fill": {"r": 250, "g": 200, "b": 0}},
            "array": {"count": 3, "startX": 400, "startY": 200, "spacingX": 15}}}, return_preview=False)
        total = probe("return app.activeDocument.pageItems.length;")
        return (ok(env) and total == 7) or f"total={total} {str(env)[:300]}"
    await step("illustrator_execute_task", "element_create_batch x3 ellipses", et_batch)

    async def et_fill_uuid():
        env, _ = await T("illustrator_execute_task", payload={"task": "style_set_fill",
            "targets": {"type": "uuid", "uuids": [IDS["ell_b"]], "document": IDS["document"]},
            "params": {"r": 0, "g": 160, "b": 80}}, return_preview=False)
        c = probe("var f = app.activeDocument.pageItems.getByName('ell_b').fillColor; return [Math.round(f.red), Math.round(f.green), Math.round(f.blue)];")
        return (ok(env) and c == [0, 160, 80]) or f"fill={c} {str(env)[:300]}"
    await step("illustrator_execute_task", "style_set_fill by uuid (read back [0,160,80])", et_fill_uuid)

    async def et_curve():
        env, _ = await T("illustrator_execute_task", payload={"task": "element_create", "params": {
            "type": "path", "points": [[300, 300], [350, 260], [400, 300], [450, 260]], "smooth": True, "tension": 0.5,
            "stroke": {"r": 0, "g": 100, "b": 200, "width": 3}}}, return_preview=False)
        return ok(env) or env
    await step("illustrator_execute_task", "smooth curve path", et_curve)

    # ---------------- illustrator_execute_task: layout, groups, layers, clip ----------------
    GRP = {"type": "query", "itemType": "PathItem", "pattern": "grp_*"}
    ORDER = ("var d = app.activeDocument, o = []; for (var i = 0; i < d.pathItems.length; i++) { var p = d.pathItems[i];"
             " if (/^grp_/.test(p.name)) o.push([p.zOrderPosition, p.name, p.parent.typename]); }"
             " o.sort(function (a, b) { return a[0] - b[0]; }); var n = []; for (var j = 0; j < o.length; j++) n.push(o[j][1] + '@' + o[j][2]); return n;")
    GEOM = ("var d = app.activeDocument, o = []; for (var i = 1; i <= 3; i++) { var r = d.pageItems.getByName('grp_' + i);"
            " o.push([Math.round(r.left), Math.round(r.top), Math.round(r.width)]); } return o;")

    async def lay_setup():
        probe("var d = app.activeDocument; for (var i = 0; i < 3; i++) { var r = d.pathItems.rectangle(-200 - i * 50, 30 + i * 70 + i * i * 15, 30 + i * 10, 30); r.name = 'grp_' + (i + 1); r.stroked = false; } return 1;")
        return probe("return app.activeDocument.pageItems.getByName('grp_3').name;") == "grp_3"
    await step("illustrator_execute_task", "setup: three named rects", lay_setup)

    async def lay_distribute():
        env, _ = await T("illustrator_execute_task", payload={"task": "distribute_horizontal", "targets": GRP, "params": {"mode": "gap"}}, return_preview=False)
        g = probe(GEOM)
        gap1 = g[1][0] - (g[0][0] + g[0][2]); gap2 = g[2][0] - (g[1][0] + g[1][2])
        return (ok(env) and gap1 == gap2) or f"gaps {gap1} vs {gap2} geometry={g}"
    await step("illustrator_execute_task", "distribute_horizontal: equal gaps (read back)", lay_distribute)

    async def lay_align_h():
        env, _ = await T("illustrator_execute_task", payload={"task": "align_horizontal", "targets": GRP, "params": {"mode": "left"}}, return_preview=False)
        g = probe(GEOM)
        return (ok(env) and g[0][0] == g[1][0] == g[2][0]) or f"lefts {[x[0] for x in g]}"
    await step("illustrator_execute_task", "align_horizontal left: same left edge", lay_align_h)

    async def lay_align_v():
        env, _ = await T("illustrator_execute_task", payload={"task": "align_vertical", "targets": GRP, "params": {"mode": "top"}}, return_preview=False)
        g = probe(GEOM)
        return (ok(env) and g[0][1] == g[1][1] == g[2][1]) or f"tops {[x[1] for x in g]}"
    await step("illustrator_execute_task", "align_vertical top: same top edge", lay_align_v)

    async def lay_group_ungroup():
        before = probe(ORDER)
        env, _ = await T("illustrator_execute_task", payload={"task": "group_create", "targets": GRP, "params": {"name": "live_group"}}, return_preview=False)
        grouped = probe("var g = app.activeDocument.groupItems, n = 0; for (var i = 0; i < g.length; i++) if (g[i].name == 'live_group') n = g[i].pageItems.length; return n;")
        mid = probe(ORDER)
        env2, _ = await T("illustrator_execute_task", payload={"task": "group_ungroup", "targets": {"type": "query", "itemType": "GroupItem", "pattern": "live_group"}}, return_preview=False)
        left = probe("var g = app.activeDocument.groupItems, n = 0; for (var i = 0; i < g.length; i++) if (g[i].name == 'live_group') n++; return n;")
        after = probe(ORDER)
        return (ok(env) and grouped == 3 and ok(env2) and left == 0 and after == before and all(x.endswith("@Layer") for x in after)) or f"group={grouped} left={left} order before={before} mid={mid} after={after} {str(env2)[:200]}"
    await step("illustrator_execute_task", "group_create then group_ungroup (z-order kept)", lay_group_ungroup)

    async def lay_layers():
        info = "var o = []; var L = app.activeDocument.layers; for (var i = 0; i < L.length; i++) o.push([L[i].name, L[i].locked, L[i].visible]); return o;"
        for task, params in [("layer_create", {"name": "LiveLayer"}), ("layer_lock", {"name": "LiveLayer", "locked": True}), ("layer_visible", {"name": "LiveLayer", "visible": False})]:
            e, _ = await T("illustrator_execute_task", payload={"task": task, "params": params}, return_preview=False)
            if not ok(e): return f"{task}: {str(e)[:300]}"
        st = [x for x in probe(info) if x[0] == "LiveLayer"]
        if st != [["LiveLayer", True, False]]: return f"state {st}"
        e, _ = await T("illustrator_execute_task", payload={"task": "layer_delete", "params": {"name": "LiveLayer"}}, return_preview=False)
        still = [x for x in probe(info) if x[0] == "LiveLayer"]
        if ok(e) or not still: return f"delete of a hidden layer should be refused: ok={ok(e)} layers={still}"
        for task, params in [("layer_visible", {"name": "LiveLayer", "visible": True}), ("layer_lock", {"name": "LiveLayer", "locked": False}), ("layer_delete", {"name": "LiveLayer"})]:
            e, _ = await T("illustrator_execute_task", payload={"task": task, "params": params}, return_preview=False)
            if not ok(e): return f"{task}: {str(e)[:300]}"
        gone = [x for x in probe(info) if x[0] == "LiveLayer"] == []
        return gone or "layer still exists"
    await step("illustrator_execute_task", "layer create/lock/hide, hidden delete refused, then delete", lay_layers)

    async def lay_clip():
        for idn, x, w in [("CM", 100, 80), ("CC", 60, 200)]:
            e, _ = await T("illustrator_execute_task", payload={"task": "element_create", "params": {"id": idn, "type": "rect", "x": x, "y": 340, "width": w, "height": 40, "fill": {"r": 9, "g": 9, "b": 9}}}, return_preview=False)
            if not ok(e): return f"element_create {idn}: {str(e)[:200]}"
        env, _ = await T("illustrator_execute_task", payload={"task": "clip_create", "params": {"mask": "CM", "contents": ["CC"]}}, return_preview=False)
        clipped = probe("var g = app.activeDocument.groupItems, n = 0; for (var i = 0; i < g.length; i++) if (g[i].clipped) n++; return n;")
        return (ok(env) and clipped >= 1) or f"clipped groups={clipped} {str(env)[:300]}"
    await step("illustrator_execute_task", "clip_create makes a clipping group", lay_clip)

    async def sel_task():
        probe("var d = app.activeDocument; var r = d.pathItems.rectangle(-300, 400, 60, 40); r.name = 'sel_1'; r.stroked = false; var c = new RGBColor(); c.red = 1; c.green = 1; c.blue = 1; r.fillColor = c; d.selection = [r]; return 1;")
        env, _ = await T("illustrator_execute_task", payload={"task": "style_set_fill", "targets": {"type": "selection"}, "params": {"r": 0, "g": 160, "b": 80}}, return_preview=False)
        fill = probe("var f = app.activeDocument.pageItems.getByName('sel_1').fillColor; return [Math.round(f.red), Math.round(f.green), Math.round(f.blue)];")
        kept = probe("return app.activeDocument.selection.length;")
        return (ok(env) and fill == [0, 160, 80] and kept == 1) or f"fill={fill} selection kept={kept} {str(env)[:200]}"
    await step("illustrator_execute_task", "selection target: fill applied and selection kept", sel_task)

    async def sel_ungroup():
        probe("var d = app.activeDocument; for (var i = 0; i < 2; i++) { var r = d.pathItems.rectangle(-340 - i * 20, 500 + i * 30, 20, 15); r.name = 'selg_' + i; } return 1;")
        await T("illustrator_execute_task", payload={"task": "group_create", "targets": {"type": "query", "itemType": "PathItem", "pattern": "selg_*"}, "params": {"name": "sel_group"}}, return_preview=False)
        probe("var d = app.activeDocument; d.selection = [d.groupItems.getByName('sel_group')]; return 1;")
        env, _ = await T("illustrator_execute_task", payload={"task": "group_ungroup", "targets": {"type": "selection"}}, return_preview=False)
        left = probe("var g = app.activeDocument.groupItems, n = 0; for (var i = 0; i < g.length; i++) if (g[i].name == 'sel_group') n++; return n;")
        return (ok(env) and left == 0) or f"groups left={left} {str(env)[:200]}"
    await step("illustrator_execute_task", "selection target: group_ungroup acts on the selected group", sel_ungroup)

    async def sel_readonly():
        probe("var d = app.activeDocument; d.selection = [d.pageItems.getByName('sel_1')]; return 1;")
        env, _ = await T("illustrator_query_items", targets={"type": "layer", "layer": "Layer 1"})
        env2, _ = await T("illustrator_query_items", targets={"type": "selection"})
        kept = probe("return app.activeDocument.selection.length;")
        return (ok(env) and ok(env2) and kept == 1) or f"read-only queries left selection={kept}"
    await step("illustrator_query_items", "read-only queries leave the selection alone", sel_readonly)

    # ---------------- illustrator_text ----------------
    TXT = lambda **k: T("illustrator_text", uuids=[IDS["txt_c"]], document=IDS["document"], **k)

    async def tx_dry():
        env, _ = await TXT(action="replace", find="hello", replace="X", dry_run=True, case_sensitive=False)
        r = res_of(env)
        return (ok(env) and r["dry_run"] and r["match_count"] == 2 and r["replaced_count"] == 0) or env
    await step("illustrator_text", "replace dry_run: 2 matches, nothing changed", tx_dry)

    async def tx_replace():
        env, _ = await TXT(action="replace", find="big", replace="enormous")
        t = probe("return app.activeDocument.pageItems.getByName('txt_c').contents;")
        return (ok(env) and t == "Hello enormous world, hello") or f"{t} {str(env)[:300]}"
    await step("illustrator_text", "replace keeps text (read back)", tx_replace)

    async def tx_batch():
        env, _ = await TXT(action="replace", replacements=[{"find": "Hello", "replace": "Howdy"}, {"find": "world", "replace": "planet"}])
        t = probe("return app.activeDocument.pageItems.getByName('txt_c').contents;")
        return (ok(env) and t == "Howdy enormous planet, hello") or f"{t} {str(env)[:300]}"
    await step("illustrator_text", "replacements batch (2 pairs)", tx_batch)

    async def tx_style():
        env, _ = await TXT(action="style", start=0, length=5, size=30, color={"hex": "#c80000"}, tracking=50)
        v = probe("var c = app.activeDocument.pageItems.getByName('txt_c').textRange.characters[0].characterAttributes; return {s: Math.round(c.size), r: Math.round(c.fillColor.red), t: c.tracking};")
        return (ok(env) and v == {"s": 30, "r": 200, "t": 50}) or f"{v} {str(env)[:300]}"
    await step("illustrator_text", "style size/color/tracking (read back)", tx_style)

    async def tx_runs():
        # chars 0-4 now size 30 / red, rest 18: a match spanning both needs replace_runs
        env, _ = await TXT(action="replace", find="Howdy enormous", replace="x")
        occ = res_of(env)["skipped_occurrences"]
        if not (occ and occ[0]["reason"] == "mixed_styles" and len(occ[0]["runs"]) == 2):
            return f"mixed match not reported: {str(env)[:300]}"
        env2, _ = await TXT(action="replace", find="Howdy enormous", replace_runs=["Hi", " huge"])
        v = probe("var tf = app.activeDocument.pageItems.getByName('txt_c'); var c = tf.textRange.characters; return {t: tf.contents, s0: Math.round(c[0].characterAttributes.size), s3: Math.round(c[3].characterAttributes.size)};")
        return (ok(env2) and v["t"].startswith("Hi huge") and v["s0"] == 30 and v["s3"] == 18) or f"{v} {str(env2)[:300]}"
    await step("illustrator_text", "replace_runs keeps each run's style", tx_runs)

    async def tx_font():
        cur = probe("return app.activeDocument.pageItems.getByName('txt_c').textRange.characters[8].characterAttributes.textFont.name;")
        target = "ArialMT" if cur != "ArialMT" else "Helvetica"
        env, _ = await T("illustrator_text", action="replace_font", from_font=cur, to_font=target)
        now = probe("return app.activeDocument.pageItems.getByName('txt_c').textRange.characters[8].characterAttributes.textFont.name;")
        return (ok(env) and now == target) or f"{cur}->{now} {str(env)[:300]}"
    await step("illustrator_text", "replace_font (read back)", tx_font)

    async def tx_outline():
        probe("var d = app.activeDocument; var t = d.textFrames.add(); t.contents = 'outline me'; t.position = [300, -350]; t.name = 'txt_out'; return 1;")
        env0, _ = await T("illustrator_inspect", view="artboard", artboard_index=0)
        uu = None
        def walk(n):
            nonlocal uu
            if isinstance(n, dict):
                if n.get("name") == "txt_out" and n.get("uuid"): uu = str(n["uuid"])
                for v in n.values(): walk(v)
            elif isinstance(n, list):
                for v in n: walk(v)
        walk(res_of(env0))
        if not uu: return "no uuid for txt_out"
        env, _ = await T("illustrator_text", action="outline", uuids=[uu], document=IDS["document"])
        left = probe("var d = app.activeDocument, n = 0; for (var i = 0; i < d.textFrames.length; i++) if (d.textFrames[i].name == 'txt_out') n++; return n;")
        return (ok(env) and left == 0) or f"frames left={left} {str(env)[:300]}"
    await step("illustrator_text", "outline converts the frame to paths", tx_outline)

    async def tx_locked_guard():
        probe("app.activeDocument.pageItems.getByName('txt_c').locked = true; return 1;")
        env, _ = await TXT(action="replace", find="Hi", replace="NO")
        r = res_of(env)
        t = probe("var tf = app.activeDocument.pageItems.getByName('txt_c'); var c = tf.contents; tf.locked = false; return c;")
        return (t.startswith("Hi") and r["replaced_count"] == 0 and bool(r["skipped_objects"])) or f"{t} {str(env)[:300]}"
    await step("illustrator_text", "locked frame is never modified", tx_locked_guard)

    async def tx_wrong_doc():
        env, _ = await T("illustrator_text", action="replace", uuids=[IDS["txt_c"]], document="other.ai", find="Hi", replace="NO")
        return (env.get("ok") is False) or env
    await step("illustrator_text", "wrong document name is refused", tx_wrong_doc)

    # ---------------- illustrator_swatches ----------------
    async def sw_list():
        env, _ = await T("illustrator_swatches", action="list")
        return ok(env) or env
    await step("illustrator_swatches", "list", sw_list)

    async def sw_group():
        env, _ = await T("illustrator_swatches", action="create_group", group="LiveBrand")
        return ok(env) or env
    await step("illustrator_swatches", "create_group", sw_group)

    async def sw_create():
        env, _ = await T("illustrator_swatches", action="create", group="LiveBrand",
                         swatches=[{"name": "Live Red", "kind": "global", "color": {"hex": "#d62828"}}])
        n = probe("var s = app.activeDocument.swatches, f = false; for (var i = 0; i < s.length; i++) if (s[i].name == 'Live Red') f = true; return f;")
        return (ok(env) and n is True) or f"found={n} {str(env)[:300]}"
    await step("illustrator_swatches", "create global swatch (read back)", sw_create)

    async def sw_get():
        env, _ = await T("illustrator_swatches", action="get", names=["Live Red"])
        return (ok(env) and "Live Red" in json.dumps(res_of(env))) or env
    await step("illustrator_swatches", "get by name", sw_get)

    async def sw_libs():
        env, _ = await T("illustrator_swatches", action="libraries")
        return ok(env) or env
    await step("illustrator_swatches", "libraries", sw_libs)

    async def sw_delete():
        env, _ = await T("illustrator_swatches", action="delete_group", group="LiveBrand", keep_swatches=False)
        n = probe("var s = app.activeDocument.swatches, f = false; for (var i = 0; i < s.length; i++) if (s[i].name == 'Live Red') f = true; return f;")
        return (ok(env) and n is False) or f"still there={n} {str(env)[:300]}"
    await step("illustrator_swatches", "delete_group removes its swatches", sw_delete)

    # ---------------- illustrator_effects ----------------
    async def fx_apply():
        a = WORK / "fx_before.png"; b = WORK / "fx_after.png"
        await T("illustrator_export_document", file_path=str(a), format="png", artboard_only=True, artboard_index=0)
        env, _ = await T("illustrator_effects", action="apply", effect="drop_shadow", uuids=[IDS["rect_a"]], document=IDS["document"],
                         offset_x=0, offset_y=8, blur=6, opacity=80)
        await T("illustrator_export_document", file_path=str(b), format="png", artboard_only=True, artboard_index=0)
        return (ok(env) and a.read_bytes() != b.read_bytes()) or f"same pixels? {a.read_bytes() == b.read_bytes()} {str(env)[:300]}"
    await step("illustrator_effects", "drop_shadow changes rendered pixels", fx_apply)

    async def fx_blur():
        env, _ = await T("illustrator_effects", action="apply", effect="gaussian_blur", uuids=[IDS["rect_a"]], document=IDS["document"], radius=3, replace=True)
        return ok(env) or env
    await step("illustrator_effects", "gaussian_blur replace=True", fx_blur)

    async def fx_remove():
        a = WORK / "fx_blur.png"; b = WORK / "fx_clean.png"
        await T("illustrator_export_document", file_path=str(a), format="png", artboard_only=True, artboard_index=0)
        env, _ = await T("illustrator_effects", action="remove", uuids=[IDS["rect_a"]], document=IDS["document"])
        await T("illustrator_export_document", file_path=str(b), format="png", artboard_only=True, artboard_index=0)
        return (ok(env) and a.read_bytes() != b.read_bytes()) or env
    await step("illustrator_effects", "remove clears effects (pixels change)", fx_remove)

    # ---------------- illustrator_path_import_svg / boolean ----------------
    async def svg_import():
        env, _ = await T("illustrator_path_import_svg", d="M 400 300 L 500 300 L 500 380 L 400 380 Z", name="svg_sq", tag="live")
        v = probe("var d = app.activeDocument, o = []; for (var i = 0; i < d.pageItems.length; i++) if (d.pageItems[i].name == 'svg_sq') o.push([d.pageItems[i].typename, d.pageItems[i].note]); return o;")
        return (ok(env) and len(v) == 1 and "live" in v[0][1]) or f"named items={v} (name param ignored?) {str(res_of(env))[:200]}"
    await step("illustrator_path_import_svg", "import path with name+tag (read back name and note)", svg_import)

    async def svg_geometry():
        env, _ = await T("illustrator_path_import_svg", d="M 10 10 L 90 10 L 90 90 Z M 30 30 L 60 30 L 60 60 Z")
        v = probe("var d = app.activeDocument; var n = d.compoundPathItems.length; return n;")
        return (ok(env) and v >= 1) or f"compound={v} {str(res_of(env))[:200]}"
    await step("illustrator_path_import_svg", "two closed subpaths -> compound path", svg_geometry)

    async def svg_fill_doc_example():
        env, _ = await T("illustrator_path_import_svg", d="M 0 0 L 100 0 L 100 100 Z", name="svg_red", fill={"r": 255, "g": 0, "b": 0})
        f = probe("var d = app.activeDocument, r = null; for (var i = 0; i < d.pageItems.length; i++) { var it = d.pageItems[i]; if (it.typename == 'PathItem' && it.pathPoints.length == 3 && it.width > 99 && it.width < 101) r = it; } return r && r.filled ? [Math.round(r.fillColor.red), Math.round(r.fillColor.green), Math.round(r.fillColor.blue)] : null;")
        return (ok(env) and f == [255, 0, 0]) or f"docstring example fill={{r:255}} -> fill read back {f}"
    await step("illustrator_path_import_svg", "docstring example with fill=... paints it red", svg_fill_doc_example)

    async def svg_bad():
        env, _ = await T("illustrator_path_import_svg", d="not a path")
        return (env.get("ok") is False) or env
    await step("illustrator_path_import_svg", "garbage d -> error", svg_bad)

    async def bool_ops():
        # two MCP-tagged shapes via element_create, then unite
        for i, (x, w) in enumerate([(100, 80), (140, 80)]):
            await T("illustrator_execute_task", payload={"task": "element_create", "params": {
                "id": f"B{i}", "type": "rect", "x": x, "y": 300, "width": w, "height": 60, "fill": {"r": 90, "g": 90, "b": 90}}}, return_preview=False)
        before = probe("return app.activeDocument.pageItems.length;")
        env, _ = await T("illustrator_path_boolean", operation="unite", subject="B0", clip=["B1"], name="united")
        after = probe("var d = app.activeDocument; var u = null; try { u = d.pageItems.getByName('united'); } catch (e) {} return {n: d.pageItems.length, found: !!u, w: u ? Math.round(u.width) : 0};")
        return (ok(env) and after["found"] and after["n"] == before - 1 and after["w"] == 120) or f"{before} {after} {str(env)[:300]}"
    await step("illustrator_path_boolean", "unite two rects -> one 120pt-wide path", bool_ops)

    async def bool_sub():
        for i, (x, y, w, h) in enumerate([(300, 320, 100, 50), (320, 330, 30, 20)]):
            await T("illustrator_execute_task", payload={"task": "element_create", "params": {
                "id": f"S{i}", "type": "rect", "x": x, "y": y, "width": w, "height": h, "fill": {"r": 0, "g": 0, "b": 0}}}, return_preview=False)
        env, _ = await T("illustrator_path_boolean", operation="subtract", subject="S0", clip=["S1"], name="holed")
        v = probe("var d = app.activeDocument, o = []; for (var i = 0; i < d.pageItems.length; i++) if (/^holed/.test(d.pageItems[i].name)) o.push([d.pageItems[i].typename, Math.round(d.pageItems[i].width), Math.round(d.pageItems[i].height)]); return o;")
        # hole fully inside: one result item 100x50 (compound with a hole)
        return (ok(env) and len(v) >= 1 and v[0][1] == 100 and v[0][2] == 50) or f"{v} {str(env)[:300]}"
    await step("illustrator_path_boolean", "subtract a hole -> one 100x50 result", bool_sub)

    async def bool_intersect():
        for i, (x, w) in enumerate([(100, 80), (140, 80)]):
            await T("illustrator_execute_task", payload={"task": "element_create", "params": {
                "id": f"I{i}", "type": "rect", "x": x, "y": 20, "width": w, "height": 30, "fill": {"r": 0, "g": 0, "b": 0}}}, return_preview=False)
        env, _ = await T("illustrator_path_boolean", operation="intersect", subject="I0", clip=["I1"], name="isect")
        v = probe("var u = app.activeDocument.pageItems.getByName('isect'); return Math.round(u.width);")
        return (ok(env) and v == 40) or f"width={v} {str(env)[:300]}"
    await step("illustrator_path_boolean", "intersect -> 40pt overlap", bool_intersect)

    async def bool_bad_id():
        env, _ = await T("illustrator_path_boolean", operation="unite", subject="nope_1", clip=["nope_2"])
        return (env.get("ok") is False) or env
    await step("illustrator_path_boolean", "unknown ids -> error", bool_bad_id)

    # ---------------- illustrator_history ----------------
    async def hist_cp():
        env, _ = await T("illustrator_history", action="checkpoint_save", name="live_cp")
        env2, _ = await T("illustrator_history", action="checkpoint_list")
        return (ok(env) and "live_cp" in json.dumps(env2)) or env
    await step("illustrator_history", "checkpoint_save + list", hist_cp)

    async def hist_undo_redo():
        probe("var r = app.activeDocument.pathItems.rectangle(-10, 10, 20, 20); r.name = 'undo_me'; return 1;")
        # undo steps are per ExtendScript call; use the tool to undo the last change
        n0 = probe("return app.activeDocument.pageItems.length;")
        envu, _ = await T("illustrator_history", action="undo", count=1)
        n1 = probe("return app.activeDocument.pageItems.length;")
        envr, _ = await T("illustrator_history", action="redo", count=1)
        n2 = probe("return app.activeDocument.pageItems.length;")
        return (ok(envu) and ok(envr) and n1 == n0 - 1 and n2 == n0) or f"{n0} {n1} {n2} {str(envu)[:200]}"
    await step("illustrator_history", "undo then redo (item count read back)", hist_undo_redo)

    async def hist_delete():
        env, _ = await T("illustrator_history", action="checkpoint_delete", name="live_cp")
        env2, _ = await T("illustrator_history", action="checkpoint_list")
        return (ok(env) and "live_cp" not in json.dumps(env2)) or env
    await step("illustrator_history", "checkpoint_delete", hist_delete)

    # ---------------- illustrator_place_file / set_reference ----------------
    png = WORK / "ref.png"
    make_png(png)

    async def place_embed():
        before = probe("return app.activeDocument.placedItems.length + app.activeDocument.rasterItems.length;")
        env, _ = await T("illustrator_place_file", file_path=str(png), x=20, y=20, linked=False)
        after = probe("return app.activeDocument.placedItems.length + app.activeDocument.rasterItems.length;")
        return (ok(env) and after == before + 1) or f"{before}->{after} {str(env)[:300]}"
    await step("illustrator_place_file", "place PNG embedded (read back)", place_embed)

    async def place_linked():
        env, _ = await T("illustrator_place_file", file_path=str(png), x=120, y=20, linked=True)
        n = probe("return app.activeDocument.placedItems.length;")
        return (ok(env) and n >= 1) or f"placed={n} {str(env)[:300]}"
    await step("illustrator_place_file", "place PNG linked", place_linked)

    async def place_missing():
        env, _ = await T("illustrator_place_file", file_path=str(WORK / "nope.png"))
        return (env.get("ok") is False) or env
    await step("illustrator_place_file", "missing file -> error", place_missing)

    async def ref_set():
        env, _ = await T("illustrator_set_reference", file_path=str(png), opacity=50)
        n = probe("var d = app.activeDocument, f = false; for (var i = 0; i < d.layers.length; i++) if (/ref/i.test(d.layers[i].name)) f = d.layers[i].name; return f;")
        return (ok(env) and bool(n)) or f"layer={n} {str(env)[:300]}"
    await step("illustrator_set_reference", "set reference creates a locked layer", ref_set)

    async def ref_clear():
        env, _ = await T("illustrator_set_reference")
        n = probe("var d = app.activeDocument, f = false; for (var i = 0; i < d.layers.length; i++) if (/ref/i.test(d.layers[i].name)) f = d.layers[i].name; return f;")
        return (ok(env) and n is False) or f"layer={n} {str(env)[:300]}"
    await step("illustrator_set_reference", "clear removes it", ref_clear)

    # ---------------- illustrator_export_document ----------------
    for fmt in ("png", "jpg", "svg", "pdf"):
        async def exp(fmt=fmt):
            p = WORK / f"out.{fmt}"
            if p.exists(): p.unlink()
            t0 = time.time()
            env, _ = await T("illustrator_export_document", file_path=str(p), format=fmt, artboard_only=True, artboard_index=0, scale=1.0)
            size = p.stat().st_size if p.exists() else 0
            print(f"      ({fmt}: {time.time() - t0:.1f}s, {size} bytes)", flush=True)
            if fmt == "svg" and size > 300_000:
                return f"svg of a document with text is {size} bytes: all glyphs of every font were embedded (expected GLYPHSUSED)"
            return (ok(env) and p.exists() and size > 500) or f"exists={p.exists()} {str(env)[:300]}"
        await step("illustrator_export_document", f"export {fmt} (file exists, >500 B)", exp)

    async def exp_renames():
        for fmt in ("svg", "pdf"):
            env, _ = await T("illustrator_export_document", file_path=str(WORK / f"rename.{fmt}"), format=fmt, artboard_only=True, artboard_index=0)
            doc = (res_of(env) or {}).get("document") if isinstance(res_of(env), dict) else None
            actual = probe("return app.activeDocument.name;")
            warned = any("active document is now the exported" in w for w in env.get("warnings", []))
            if not (ok(env) and doc and doc["renamed"] and doc["after"] == f"rename.{fmt}" == actual and warned):
                return f"{fmt}: report={doc} actual={actual} warned={warned}"
        return True
    await step("illustrator_export_document", "svg/pdf report that the document was re-pointed (read back)", exp_renames)

    async def exp_img():
        env, imgs = await T("illustrator_export_document", file_path=str(WORK / "ret.png"), format="png", return_image=True, artboard_only=True, artboard_index=0)
        return (ok(env) and len(imgs) >= 1) or f"images={len(imgs)} {str(env)[:300]}"
    await step("illustrator_export_document", "return_image gives an image", exp_img)

    async def exp_scale():
        from PIL import Image
        await T("illustrator_export_document", file_path=str(WORK / "s1.png"), format="png", scale=1.0, artboard_only=True, artboard_index=0)
        await T("illustrator_export_document", file_path=str(WORK / "s2.png"), format="png", scale=2.0, artboard_only=True, artboard_index=0)
        a = Image.open(WORK / "s1.png").size; b = Image.open(WORK / "s2.png").size
        return (b[0] == 2 * a[0]) or f"{a} {b}"
    await step("illustrator_export_document", "scale=2 doubles pixel width", exp_scale)

    # ---------------- illustrator_preflight_check ----------------
    async def pf_all():
        env, _ = await T("illustrator_preflight_check")
        return ok(env) or env
    await step("illustrator_preflight_check", "default (all scopes)", pf_all)

    async def pf_scoped():
        env, _ = await T("illustrator_preflight_check", scopes=["text", "images"], min_ppi=300)
        s = json.dumps(res_of(env))
        return (ok(env) and "low_ppi" in s) or f"{s[:300]}"
    await step("illustrator_preflight_check", "scopes text+images flags the 64px image (low_ppi)", pf_scoped)

    async def pf_offboard():
        probe("var r = app.activeDocument.pathItems.rectangle(-500, 900, 40, 40); r.name = 'far_away'; return 1;")
        env, _ = await T("illustrator_preflight_check", scopes=["objects"], artboard_index=0)
        s = json.dumps(res_of(env))
        probe("app.activeDocument.pageItems.getByName('far_away').remove(); return 1;")
        return (ok(env) and "off_artboard" in s) or f"{s[:300]}"
    await step("illustrator_preflight_check", "off-artboard object is reported", pf_offboard)

    # ---------------- illustrator_place_file: trace and editable PDF ----------------
    CLEAR = "var d = app.activeDocument; while (d.pageItems.length) d.pageItems[0].remove(); return 1;"
    COUNTS = "var d = app.activeDocument; return {placed: d.placedItems.length, raster: d.rasterItems.length, plugin: d.pluginItems.length, paths: d.pathItems.length, groups: d.groupItems.length};"

    async def tr_linked():
        probe(CLEAR)
        env, _ = await T("illustrator_place_file", file_path=str(png), x=30, y=30, linked=True, trace=True)
        c = probe(COUNTS)
        return (ok(env) and c["paths"] > 20 and c["groups"] >= 1 and c["placed"] == 0) or f"{c} {str(env)[:300]}"
    await step("illustrator_place_file", "trace a linked PNG -> expanded paths", tr_linked)

    async def tr_embedded():
        probe(CLEAR)
        env, _ = await T("illustrator_place_file", file_path=str(png), x=30, y=30, linked=False, trace=True)
        c = probe(COUNTS)
        return (ok(env) and c["paths"] > 20 and c["raster"] == 0) or f"embedded trace: {c} {str(env)[:300]}"
    await step("illustrator_place_file", "trace an embedded PNG (marker survives embed)", tr_embedded)

    async def tr_preset():
        probe(CLEAR)
        env, _ = await T("illustrator_place_file", file_path=str(png), x=30, y=30, linked=True, trace=True, trace_preset="6 Colors")
        c = probe(COUNTS)
        return (ok(env) and 1 <= c["paths"] <= 12) or f"{c} {str(env)[:300]}"
    await step("illustrator_place_file", "trace preset '6 Colors' -> at most 12 paths", tr_preset)

    async def tr_unknown_preset():
        probe(CLEAR)
        env, _ = await T("illustrator_place_file", file_path=str(png), x=30, y=30, linked=True, trace=True, trace_preset="No Such Preset")
        w = (res_of(env) or {}).get("warnings") if isinstance(res_of(env), dict) else None
        return (ok(env) and bool(w) and "Preset not found" in json.dumps(w)) or f"warnings={w} {str(env)[:200]}"
    await step("illustrator_place_file", "unknown trace preset is reported", tr_unknown_preset)

    async def tr_live():
        probe(CLEAR)
        env, _ = await T("illustrator_place_file", file_path=str(png), x=30, y=30, linked=True, trace=True, expand=False)
        c = probe(COUNTS)
        return (ok(env) and c["plugin"] == 1 and c["paths"] == 0) or f"{c} {str(env)[:300]}"
    await step("illustrator_place_file", "expand=False keeps a live trace (plugin item)", tr_live)

    async def pdf_editable():
        probe(CLEAR)
        import shutil
        src = WORK / "editable_src.pdf"      # a copy: after the export step the active document IS out.pdf
        shutil.copy(WORK / "out.pdf", src)
        env, _ = await T("illustrator_place_file", file_path=str(src), x=10, y=10, embed_editable=True)
        c = probe(COUNTS)
        names = probe("var o = []; for (var i = 0; i < app.documents.length; i++) o.push(app.documents[i].name); return o;")
        # the pasted PDF carries the earlier test content (including images), so count vectors, not placed items
        return (ok(env) and c["paths"] > 0 and len(names) == len(PRE_NAMES) + 1) or f"{c} docs={names} {str(env)[:300]}"
    await step("illustrator_place_file", "embed_editable opens a PDF as vectors (document intact)", pdf_editable)

    async def trace_vector_refused():
        try:
            env, _ = await T("illustrator_place_file", file_path=str(WORK / "out.pdf"), trace=True)
        except Exception as e:
            return "raster" in str(e) or str(e)[:200]
        return "tracing a PDF should be refused"
    await step("illustrator_place_file", "trace of a non-raster file is rejected by validation", trace_vector_refused)

    # ---------------- illustrator_ground_object ----------------
    async def ground():
        env, imgs = await T("illustrator_execute_script", script="1", return_preview=True, preview_mode="annotated", preview_max_items=50)
        if not imgs:
            return f"no annotated preview image: {str(env)[:300]}"
        env2, _ = await T("illustrator_ground_object", label=1)
        return (ok(env2)) or env2
    await step("illustrator_ground_object", "ground label [1] from the annotated preview", ground)

    async def ground_bad():
        env, _ = await T("illustrator_ground_object", label=400)
        return (env.get("ok") is False) or env
    async def ground_range():
        try:
            await T("illustrator_ground_object", label=9999)
        except Exception as e:
            return "less than or equal to 500" in str(e) or str(e)[:200]
        return "label 9999 was accepted"
    await step("illustrator_ground_object", "label above 500 is rejected by validation", ground_range)
    await step("illustrator_ground_object", "label with no match in the preview -> error", ground_bad)

    # ---------------- document save / open / switch / close ----------------
    async def doc_save():
        p = WORK / "t1.ai"
        if p.exists(): p.unlink()
        env, _ = await T("illustrator_document", action="save", file_path=str(p))
        return (ok(env) and p.exists() and p.stat().st_size > 1000) or f"exists={p.exists()} {str(env)[:300]}"
    await step("illustrator_document", "save as .ai (file exists)", doc_save)

    async def doc_close_open():
        env, _ = await T("illustrator_document", action="close", save_before_close=False)
        n0 = probe("return app.documents.length;")
        env2, _ = await T("illustrator_document", action="open", file_path=str(WORK / "t1.ai"))
        n1 = probe("return app.documents.length;")
        return (ok(env) and n0 == len(PRE_NAMES) and ok(env2) and n1 == len(PRE_NAMES) + 1) or f"{n0} {n1} {str(env2)[:300]}"
    await step("illustrator_document", "close, then reopen the saved file", doc_close_open)

    async def doc_switch():
        await T("illustrator_document", action="create", width=300, height=200, name="mcp-live-other")
        env, _ = await T("illustrator_document", action="switch", name="t1.ai")
        a = probe("return app.activeDocument.name;")
        return (ok(env) and a == "t1.ai") or f"active={a} {str(env)[:300]}"
    await step("illustrator_document", "switch between two documents", doc_switch)

    async def doc_cleanup():
        # close every document this run created, never the pre-existing blank ones
        for _ in range(6):
            left = probe("var o = []; for (var i = 0; i < app.documents.length; i++) o.push(app.documents[i].name); return o;")
            extra = [n for n in left if n not in PRE_NAMES]
            if not extra:
                break
            probe("for (var i = 0; i < app.documents.length; i++) if (app.documents[i].name == %s) { app.documents[i].activate(); break; } return 1;" % json.dumps(extra[0]))
            await T("illustrator_document", action="close", save_before_close=False)
        left = probe("var o = []; for (var i = 0; i < app.documents.length; i++) o.push(app.documents[i].name); return o;")
        return sorted(left) == sorted(PRE_NAMES) or f"documents left open: {left}"
    await step("illustrator_document", "close all test documents without saving", doc_cleanup)

    # ---------------- summary ----------------
    print()
    bad = [r for r in RESULTS if not r[2]]
    print(f"TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(bad)}  FAIL {len(bad)}")
    for t, s_, _, d in bad:
        print(f"  FAIL {t} / {s_}: {d}")
    json.dump(RESULTS, open(SP / "live_results.json", "w"), ensure_ascii=False, indent=1)
    print(f"results and exported files: {SP}")

if __name__ == '__main__':
    asyncio.run(main())
