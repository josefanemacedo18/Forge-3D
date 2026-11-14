# Forge 3D — exportador do SketchUp (alternativa)
#
# O plugin usa primeiro o leitor de SketchUp do próprio Revit. Este script só
# entra quando o Revit não consegue ler o arquivo (por exemplo, versão do
# SketchUp mais nova que o leitor do Revit).
#
# Executado pelo plugin com:
#   SketchUp.exe -RubyStartup forge3d_export.rb
# e a variável de ambiente FORGE3D_JOB apontando para um JSON:
#   { "input": "C:\\modelo.skp", "output": "C:\\saida.obj" }
#
# Gera OBJ em milímetros com Z para cima, um objeto por grupo/componente e as
# cores dos materiais num .mtl. Ao terminar cria "<saida>.ok" e fecha o SketchUp.

require 'json'

module Forge3D
  module Exporter
    def self.run
      job_path = ENV['FORGE3D_JOB']
      return unless job_path && File.exist?(job_path)
      job = JSON.parse(File.read(job_path, encoding: 'UTF-8'))
      output = job['output']
      begin
        Sketchup.open_file(job['input'])
        model = Sketchup.active_model
        @offset = 1
        @materials = {}
        File.open(output, 'w:UTF-8') do |f|
          f.puts '# forge3d units=mm up=Z'
          f.puts "mtllib #{File.basename(output, '.*')}.mtl"
          collect(model.entities, Geom::Transformation.new, f, 'Modelo', nil)
        end
        File.open(output.sub(/\.obj\z/i, '') + '.mtl', 'w:UTF-8') do |m|
          @materials.each do |name, c|
            m.puts "newmtl #{name}"
            m.puts format('Kd %.4f %.4f %.4f', c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
          end
        end
        File.write(output + '.ok', 'ok')
      rescue StandardError => e
        File.write(output + '.erro', "#{e.class}: #{e.message}") rescue nil
      ensure
        Sketchup.quit
      end
    end

    def self.safe(name)
      s = name.to_s.gsub(/[\s#]+/, '_')
      s.empty? ? 'Peca' : s
    end

    def self.material_name(mat)
      return nil unless mat
      c = mat.color
      name = format('cor_%02X%02X%02X', c.red, c.green, c.blue)
      @materials[name] ||= [c.red, c.green, c.blue]
      name
    end

    def self.collect(entities, tr, f, name, inherited)
      faces = entities.grep(Sketchup::Face)
      write_faces(faces, tr, f, name, inherited) unless faces.empty?
      entities.grep(Sketchup::Group).each_with_index do |g, i|
        n = g.name.to_s.empty? ? "#{name}_grupo#{i + 1}" : g.name
        collect(g.entities, tr * g.transformation, f, n, g.material || inherited)
      end
      entities.grep(Sketchup::ComponentInstance).each_with_index do |c, i|
        n = c.name.to_s.empty? ? "#{c.definition.name}_#{i + 1}" : c.name
        collect(c.definition.entities, tr * c.transformation, f, n, c.material || inherited)
      end
    end

    def self.write_faces(faces, tr, f, name, inherited)
      f.puts "o #{safe(name)}"
      faces.each do |face|
        mat = material_name(face.material || inherited)
        f.puts "usemtl #{mat}" if mat
        mesh = face.mesh(0)
        points = (1..mesh.count_points).map { |i| tr * mesh.point_at(i) }
        points.each { |p| f.puts format('v %.6f %.6f %.6f', p.x.to_mm, p.y.to_mm, p.z.to_mm) }
        mesh.polygons.each do |poly|
          idx = poly.map { |i| i.abs - 1 + @offset }
          (1...(idx.length - 1)).each { |k| f.puts "f #{idx[0]} #{idx[k]} #{idx[k + 1]}" }
        end
        @offset += points.length
      end
    end
  end
end

# O SketchUp executa o script antes de terminar de abrir; o temporizador
# espera a aplicação ficar pronta.
UI.start_timer(1.0, false) { Forge3D::Exporter.run }
