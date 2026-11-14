-- Forge 3D — exportador do 3ds Max
-- Converte um arquivo .max (ou qualquer formato que o 3ds Max importe, como
-- .fbx e .3ds) em OBJ em milímetros, com Z para cima, um objeto por peça e as
-- cores dos materiais num arquivo .mtl ao lado.
--
-- Uso (feito automaticamente pelo plugin):
--   3dsmaxbatch.exe forge3d_export.ms -mxsString input:"C:\modelo.max" -mxsString output:"C:\saida.obj"

(
	local inPath = maxOps.mxsCmdLineArgs[#input]
	local outPath = maxOps.mxsCmdLineArgs[#output]

	fn forgeLog msg = ( format "forge3d: %\n" msg )

	-- Fator de conversão da unidade do sistema para milímetros.
	fn unitToMm =
	(
		local f = case units.SystemType of
		(
			#inches: 25.4
			#feet: 304.8
			#miles: 1609344.0
			#millimeters: 1.0
			#centimeters: 10.0
			#meters: 1000.0
			#kilometers: 1000000.0
			default: 1.0
		)
		f * units.SystemScale
	)

	fn safeName txt =
	(
		local s = ""
		for i = 1 to txt.count do
		(
			local c = txt[i]
			s += (if c == " " or c == "#" or c == "\t" then "_" else c)
		)
		if s == "" then "Peca" else s
	)

	-- Cor do material (Standard, Physical, Arnold) ou a cor do objeto.
	fn objectColor obj =
	(
		local m = obj.material
		if classOf m == Multimaterial and m.materialList.count > 0 do m = m.materialList[1]
		local c = undefined
		if m != undefined do
		(
			if isProperty m #diffuse then c = m.diffuse
			else if isProperty m #base_color then c = m.base_color
			else if isProperty m #base_color_color then c = m.base_color_color
		)
		if classOf c != Color do c = obj.wirecolor
		c
	)

	if inPath == undefined or outPath == undefined then
	(
		forgeLog "argumentos ausentes (input/output)"
	)
	else
	(
		local ext = toLower (getFilenameType inPath)
		local loaded = false
		if ext == ".max" then
			loaded = loadMaxFile inPath useFileUnits:true quiet:true
		else
		(
			resetMaxFile #noPrompt
			loaded = importFile inPath #noPrompt
		)
		if not loaded then forgeLog ("não foi possível abrir " + inPath)
		else
		(
			local scale = unitToMm()
			local mtlPath = (getFilenamePath outPath) + (getFilenameFile outPath) + ".mtl"
			local fs = createFile outPath
			local fm = createFile mtlPath
			format "# forge3d units=mm up=Z\n" to:fs
			format "mtllib %\n" (filenameFromPath mtlPath) to:fs
			local offset = 1
			local count = 0
			local materials = #()
			for obj in geometry where (not obj.isHiddenInVpt) and (canConvertTo obj TriMeshGeometry) do
			(
				local m = undefined
				try ( m = snapshotAsMesh obj ) catch ( m = undefined )
				if m != undefined do
				(
					local nv = getNumVerts m
					local nf = getNumFaces m
					if nv > 0 and nf > 0 do
					(
						count += 1
						local c = objectColor obj
						local matName = "cor_" + (bit.intAsHex (c.r as integer)) + "_" + (bit.intAsHex (c.g as integer)) + "_" + (bit.intAsHex (c.b as integer))
						if findItem materials matName == 0 do
						(
							append materials matName
							format "newmtl %\nKd % % %\n" matName (c.r / 255.0) (c.g / 255.0) (c.b / 255.0) to:fm
						)
						format "o %\n" (safeName obj.name) to:fs
						format "usemtl %\n" matName to:fs
						for i = 1 to nv do
						(
							local p = (getVert m i) * scale
							format "v % % %\n" p.x p.y p.z to:fs
						)
						for i = 1 to nf do
						(
							local f = getFace m i
							format "f % % %\n" ((f.x as integer) + offset - 1) ((f.y as integer) + offset - 1) ((f.z as integer) + offset - 1) to:fs
						)
						offset += nv
					)
					free m
				)
			)
			close fs
			close fm
			forgeLog ("exportados " + (count as string) + " objetos para " + outPath)
		)
	)
)
