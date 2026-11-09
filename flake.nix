{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      devShells.${system}.default = pkgs.mkShell.override {
        stdenv = pkgs.clangStdenv;
      } {
        packages = with pkgs; [
          cmake
          python3
          # Cross-compiles the board programs, linked statically so they run on the KV260's Ubuntu
          pkgsCross.aarch64-multiplatform-musl.stdenv.cc
        ];
        env = {
          # The pip wheels (numpy, torch, librosa) need libstdc++ and zlib, which NixOS has no global copy of
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];
        };
      };
    };
}
