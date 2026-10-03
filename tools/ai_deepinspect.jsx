// deep walk: layer > chain group > subgroup > path counts + bounds
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var doc = app.open(new File("D:/A_task/1_Project/1_picture/flat_trace/out2/flat_palette.ai"));
var report = "";
function log(s) { report += s + "\n"; }
function walk(node, depth) {
    var pad = "";
    for (var i = 0; i < depth; i++) pad += "  ";
    var bTxt = "";
    if (node.typename != "Layer" && node.visibleBounds) {
        var b = node.visibleBounds;
        bTxt = " bounds=[" + Math.round(b[0]) + "," + Math.round(b[1]) +
               "," + Math.round(b[2]) + "," + Math.round(b[3]) + "]";
    }
    var extra = "";
    if (node.typename == "GroupItem")
        extra = " paths=" + node.pathItems.length;
    log(pad + node.typename + " '" + node.name + "'" + extra + bTxt);
    if (depth < 4 && (node.typename == "GroupItem" || node.typename == "Layer"))
        for (var c = 0; c < node.groupItems.length; c++)
            walk(node.groupItems[c], depth + 1);
}
for (var i = 0; i < doc.layers.length; i++)
    walk(doc.layers[i], 0);
log("pageItems total=" + doc.pageItems.length);
var f = new File("D:/A_task/1_Project/1_picture/flat_trace/out2/_deep_report.txt");
f.encoding = "UTF-8"; f.open("w"); f.write(report); f.close();
doc.close(SaveOptions.DONOTSAVECHANGES);
"done";
