// full render (ink + fills) for visual check
var svgFile = new File("D:/A_task/1_Project/1_picture/flat_trace/out/flat_mono.svg");
var outFile = new File("D:/A_task/1_Project/1_picture/flat_trace/out/_ai_render.png");
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var doc = app.open(svgFile);
var opts = new ExportOptionsPNG24();
opts.antiAliasing = true;
opts.horizontalScale = 100;
opts.verticalScale = 100;
opts.transparency = false;
doc.exportFile(outFile, ExportType.PNG24, opts);
doc.close(SaveOptions.DONOTSAVECHANGES);
"done";
