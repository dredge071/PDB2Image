Set ai = CreateObject("Illustrator.Application")
ai.DoJavaScript(GetFileContents("D:\A_task\1_Project\1_picture\flat_trace\ai_layercheck.jsx"))
Function GetFileContents(p)
  Dim fso, f
  Set fso = CreateObject("Scripting.FileSystemObject")
  Set f = fso.OpenTextFile(p, 1)
  GetFileContents = f.ReadAll()
  f.Close
End Function
