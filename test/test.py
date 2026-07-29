# test/test.py
# cocotb testbench for tt_um_pmunnangi_qkt_mac
# Covers: Q-load, K-load, dot-product correctness, Q-reuse across K vectors,
# accumulator reset, signed arithmetic, overflow detection, and a
# 100-sequence randomized regression.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# ---------- Command encoding (must match RTL) ----------
CMD_NOP       = 0
CMD_LOAD_Q    = 1
CMD_LOAD_K    = 2
CMD_READ_ACC  = 3
CMD_RESET_ACC = 4

# ---------- uio bit map (must match wrapper) ----------
# uio_in[2:0] = cmd, uio_in[3] = valid_in
# uio_out[4]  = ready_out, [5] = busy, [6] = overflow, [7] = done


# ============================================================
# Helpers
# ============================================================

def to_uint8(x):
    """Two's complement pack for signed byte -> unsigned 8-bit."""
    return x & 0xFF

def from_uint32(x):
    """Interpret 32-bit unsigned as signed."""
    if x & 0x8000_0000:
        return x - (1 << 32)
    return x

async def reset_dut(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

async def send_cmd(dut, cmd, data=0):
    """Drive one cycle with (cmd, valid_in=1, data_in=data)."""
    dut.ui_in.value  = to_uint8(data)
    dut.uio_in.value = (1 << 3) | (cmd & 0x7)
    await RisingEdge(dut.clk)
    dut.uio_in.value = 0
    dut.ui_in.value  = 0

async def load_q(dut, q_vec):
    assert len(q_vec) == 4
    for b in q_vec:
        await send_cmd(dut, CMD_LOAD_Q, b)

async def load_k_and_compute(dut, k_vec):
    """Load 4 K bytes, then wait for pipeline to drain."""
    assert len(k_vec) == 4
    for b in k_vec:
        await send_cmd(dut, CMD_LOAD_K, b)
    for _ in range(10):
        await RisingEdge(dut.clk)
        if (int(dut.uio_out.value) >> 7) & 1:
            return
    raise RuntimeError("Timeout waiting for done")

async def read_acc(dut):
    """Issue READ_ACC, capture 4 output bytes MSB-first."""
    await send_cmd(dut, CMD_READ_ACC, 0)
    result = 0
    for i in range(4):
        await RisingEdge(dut.clk)
        result = (result << 8) | int(dut.uo_out.value)
    await ClockCycles(dut.clk, 2)
    return from_uint32(result)

async def reset_acc(dut):
    await send_cmd(dut, CMD_RESET_ACC, 0)
    await ClockCycles(dut.clk, 1)


# ============================================================
# Reference model
# ============================================================

def dot4_signed(q, k):
    return sum((qi if qi < 128 else qi - 256) * (ki if ki < 128 else ki - 256)
               for qi, ki in zip(q, k))


# ============================================================
# Tests
# ============================================================

@cocotb.test()
async def test_basic_positive_dot_product(dut):
    """Simple positive-only dot product."""
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())  # 50 MHz
    await reset_dut(dut)

    q = [1, 2, 3, 4]
    k = [5, 6, 7, 8]

    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)

    expected = dot4_signed(q, k)   # 1*5 + 2*6 + 3*7 + 4*8 = 70
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_signed_negative_values(dut):
    """Mix of positive/negative signed INT8 values."""
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    q = [to_uint8(-10), to_uint8(20), to_uint8(-30), to_uint8(40)]
    k = [to_uint8(5),   to_uint8(-6), to_uint8(7),   to_uint8(-8)]

    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)

    expected = dot4_signed(q, k)
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_q_reuse_across_k_vectors(dut):
    """
    Core M4 story: load Q once, reuse across N K vectors.
    """
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    q = [to_uint8(2), to_uint8(-3), to_uint8(4), to_uint8(-5)]
    await load_q(dut, q)

    k_vectors = [
        [1, 1, 1, 1],
        [to_uint8(-2), to_uint8(-2), to_uint8(-2), to_uint8(-2)],
        [10, 0, 10, 0],
        [to_uint8(-1), 2, to_uint8(-3), 4],
        [7, 7, 7, 7],
    ]

    expected = 0
    for k in k_vectors:
        await load_k_and_compute(dut, k)
        expected += dot4_signed(q, k)

    result = await read_acc(dut)
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_reset_accumulator(dut):
    """RESET_ACC clears the running sum without touching Q."""
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    q = [10, 10, 10, 10]
    k = [1, 2, 3, 4]

    await load_q(dut, q)
    await load_k_and_compute(dut, k)   # acc = 100

    await reset_acc(dut)

    await load_k_and_compute(dut, k)   # acc = 100 again
    result = await read_acc(dut)
    assert result == 100, f"got {result}, expected 100"


@cocotb.test()
async def test_max_magnitude_no_overflow(dut):
    """Max signed INT8 * max signed INT8 * 4 lanes, no accumulation."""
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    q = [to_uint8(-128)] * 4
    k = [127] * 4

    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)

    expected = 4 * (-128) * 127       # -65024
    assert result == expected, f"got {result}, expected {expected}"

    overflow_bit = (int(dut.uio_out.value) >> 6) & 1
    assert overflow_bit == 0, "overflow should NOT be set"


@cocotb.test()
async def test_equal_magnitude_same_sign(dut):
    """Regression for the class of bug you hit on M4's FP32 adder."""
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    q = [50, 50, 50, 50]
    k = [50, 50, 50, 50]

    await load_q(dut, q)
    await load_k_and_compute(dut, k)
    result = await read_acc(dut)

    expected = 4 * 50 * 50
    assert result == expected, f"got {result}, expected {expected}"


@cocotb.test()
async def test_zero_operands(dut):
    """Sanity: zero in -> zero out."""
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    await load_q(dut, [0, 0, 0, 0])
    await load_k_and_compute(dut, [127, to_uint8(-128), 55, to_uint8(-77)])
    result = await read_acc(dut)
    assert result == 0, f"got {result}, expected 0"


@cocotb.test()
async def test_randomized_regression(dut):
    """100 random Q/K sequences vs Python reference model."""
    import random
    random.seed(0xC0C07B)

    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())
    await reset_dut(dut)

    NUM_SEQUENCES = 100

    for seq in range(NUM_SEQUENCES):
        await reset_acc(dut)

        q_signed = [random.randint(-128, 127) for _ in range(4)]
        q = [to_uint8(x) for x in q_signed]
        await load_q(dut, q)

        num_k = random.randint(1, 8)
        expected = 0
        for _ in range(num_k):
            k_signed = [random.randint(-128, 127) for _ in range(4)]
            k = [to_uint8(x) for x in k_signed]
            await load_k_and_compute(dut, k)
            expected += dot4_signed(q, k)

        result = await read_acc(dut)
        assert result == expected, (
            f"seq {seq}: q={q_signed}, num_k={num_k}, "
            f"got {result}, expected {expected}"
        )

    dut._log.info(f"Randomized regression passed: {NUM_SEQUENCES} sequences")
