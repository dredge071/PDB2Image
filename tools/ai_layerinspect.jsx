// inspect layer contents of flat_palette.ai: per layer, count pathItems
// and report bounds, plus top-level children names.
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var doc = app.open(new File("D:/A_task/1_Project/1_picture/flat_trace/out2/flat_palette.ai"));
var report = "";
function log(s) { report += s + "\n"; }
log("layers=" + doc.layers.length);
for (var i = 0; i < doc.layers.length; i++) {
    var L = doc.layers[i];
    log("LAYER '" + L.name + "' pathItems=" + L.pathItems.length +
        " groupItems=" + L.groupItems.length);
    for (var c = 1; c <= L.groupItems.length; c++) {
        var g = L.groupItems[c - 1];
        log("   sub '" + g.name + "' paths=" + g.pathItems.length +
            " groups=" + g.groupItems.length +
            " bounds=" + g.visibleBounds);
    }
    // sample direct pathItems bounds
    if (L.pathItems.length > 0 && L.groupItems.length == 0)
        log("   direct paths bounds=" + L.visibleBounds);
}
var f = new File("D:/A_task/1_Project/1_picture/flat_trace/out2/_inspect_report.txt");
f.encoding = "UTF-8"; f.open("w"); f.write(report); f.close();
doc.close(SaveOptions.DONOTSAVECHANGES);
"done";
