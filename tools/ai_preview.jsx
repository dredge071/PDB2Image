// export fills-only render (hide Ink + Background) for shortfall audit
var svgFile = new File("D:/A_task/1_Project/1_picture/flat_trace/out/flat_mono.svg");
var outFile = new File("D:/A_task/1_Project/1_picture/flat_trace/out/_ai_fills.png");
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var doc = app.open(svgFile);
// hide the Ink layer and background rect: lock others, hide ink
for (var i = 0; i < doc.layers.length; i++) {
    var ly = doc.layers[i];
    if (ly.name.replace(/_/g, " ") == "Ink" || ly.name == "Ink") {
        ly.visible = false;
    }
}
var opts = new ExportOptionsPNG24();
opts.antiAliasing = true;
opts.horizontalScale = 100;
opts.verticalScale = 100;
opts.transparency = false;
doc.exportFile(outFile, ExportType.PNG24, opts);
doc.close(SaveOptions.DONOTSAVECHANGES);
"fills-only export done";
