-- my hypr config (Hyprland 0.57+, Lua)
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })
hl.config({
    general = {
        gaps_in = 6,
    },
})
hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))
