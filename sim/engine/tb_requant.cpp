#include "Vstt_requant.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>
#include <array>
#include <random>
int main() {
    Vstt_requant dut;
    std::ifstream input("requant.txt");
    std::vector<std::array<int32_t,6>> cases;
    std::array<int32_t,6> c;
    while(input >> c[0] >> c[1] >> c[2] >> c[3] >> c[4] >> c[5]) cases.push_back(c);
    if(cases.empty()) return 2;
    auto tick = [&]() { dut.clk=0; dut.eval(); dut.clk=1; dut.eval(); };
    dut.rst=1; tick(); dut.rst=0;
    std::mt19937 rng(42);
    size_t sent=0, received=0;
    for(size_t cycle=0; cycle<cases.size()*20 && received<cases.size(); ++cycle) {
        dut.clk=0;
        dut.s_valid=sent<cases.size() && rng()%4!=0;
        dut.m_ready=rng()%3!=0;
        if(sent<cases.size()) {
            auto v=cases[sent];
            dut.s_acc=v[0]; dut.s_mult=v[1]; dut.s_shift=v[2]; dut.s_lo=v[3]; dut.s_hi=v[4];
        }
        dut.eval();
        bool accept=dut.s_valid && dut.s_ready;
        if(dut.m_valid && dut.m_ready) {
            if(dut.m_error || int32_t(dut.m_value)!=cases[received][5]) {
                std::cerr << "FAIL requant " << received << " got " << int32_t(dut.m_value) << " expected " << cases[received][5] << '\n';
                return 1;
            }
            ++received;
        }
        dut.clk=1; dut.eval();
        if(accept) ++sent;
    }
    if(received!=cases.size()) return 1;
    std::cout << "PASS requant: " << received << " cases with valid gaps/backpressure\n";
}
