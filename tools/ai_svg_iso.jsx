// isolation test: open the SVG directly (NO layer moves), hide all
// top-level groups except Chain_A, export PNG, then restore.
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var doc = app.open(new File("D:/A_task/1_Project/1_picture/flat_trace/out2/flat_palette.svg"));
var report = "";
function log(s) { report += s + "\n"; }
log("layers=" + doc.layers.length + " groupItems=" + doc.groupItems.length);

var L = doc.layers[0];
var tops = [];
for (var g = 0; g < L.groupItems.length; g++) {
    var gi = L.groupItems[g];
    var b = gi.visibleBounds;
    log("top group '" + gi.name + "' bounds=[" + Math.round(b[0]) + "," +
        Math.round(b[1]) + "," + Math.round(b[2]) + "," + Math.round(b[3]) + "]");
    tops.push(gi);
}
// hide everything except the group named Chain A / Chain_A
for (var i = 0; i < tops.length; i++) {
    var nm = tops[i].name;
    if (nm != "Chain_A" && nm != "Chain A") tops[i].hidden = true;
}
var opts = new ExportOptionsPNG24();
opts.antiAliasing = true;
opts.horizontalScale = 50; opts.verticalScale = 50;
opts.transparency = false;
doc.exportFile(new File("D:/A_task/1_Project/1_picture/flat_trace/out2/_isoA.png"),
               ExportType.PNG24, opts);
for (var i = 0; i < tops.length; i++) tops[i].hidden = false;
doc.close(SaveOptions.DONOTSAVECHANGES);
var f = new File("D:/A_task/1_Project/1_picture/flat_trace/out2/_iso_report.txt");
f.encoding = "UTF-8"; f.open("w"); f.write(report); f.close();
"done";
