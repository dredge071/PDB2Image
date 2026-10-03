// layer-separation check: for each chain layer, hide the other two and
// export a PNG. If ink is properly split per chain, each PNG shows only
// that chain's own linework.
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var doc = app.open(new File("D:/A_task/1_Project/1_picture/flat_trace/out2/flat_palette.ai"));
var report = "";
function log(s) { report += s + "\n"; }

var chainLayers = [];
for (var i = 0; i < doc.layers.length; i++) {
    var nm = doc.layers[i].name;
    if (/Chain [_ABC]$/.test(nm) || /^Chain_[ABC]$/.test(nm))
        chainLayers.push(doc.layers[i]);
}
log("chain layers found: " + chainLayers.length);

var opts = new ExportOptionsPNG24();
opts.antiAliasing = true;
opts.horizontalScale = 50;
opts.verticalScale = 50;
opts.transparency = false;

for (var c = 0; c < chainLayers.length; c++) {
    for (var k = 0; k < chainLayers.length; k++)
        chainLayers[k].visible = (k == c);
    var out = new File("D:/A_task/1_Project/1_picture/flat_trace/out2/_lyr_" +
                       chainLayers[c].name.replace(/[^A-Z]/g, "") + ".png");
    doc.exportFile(out, ExportType.PNG24, opts);
    log("exported " + out.fsName);
}
// restore all visible
for (var k = 0; k < chainLayers.length; k++) chainLayers[k].visible = true;
doc.close(SaveOptions.DONOTSAVECHANGES);

var f = new File("D:/A_task/1_Project/1_picture/flat_trace/out2/_lyr_report.txt");
f.encoding = "UTF-8"; f.open("w"); f.write(report); f.close();
"done";
