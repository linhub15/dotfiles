vim.opt.number = true
vim.opt.relativenumber = true

vim.g.mapleader = " "

vim.keymap.set("i", "jj", "<Esc>")
-- vim.keymap.set("n", "<leader>pv", vim.cmd.Ex, { desc = "Open netrw" } )

vim.keymap.set("n", "<leader>x", vim.cmd.Lazy, { desc = "Lazy plugin manager" })
