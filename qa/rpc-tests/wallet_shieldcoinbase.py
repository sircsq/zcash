#!/usr/bin/env python3
# Copyright (c) 2017-2024 The Zcash developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .

from test_framework.test_framework import BitcoinTestFramework
from test_framework.authproxy import JSONRPCException
from test_framework.util import assert_equal, initialize_chain_clean, \
    start_node, connect_nodes_bi, sync_blocks, sync_mempools, \
    wait_and_assert_operationid_status, get_coinbase_address, \
    NU5_BRANCH_ID, nuparams
from test_framework.zip317 import conventional_fee, ZIP_317_FEE

from decimal import Decimal

class WalletShieldCoinbaseTest (BitcoinTestFramework):
    def setup_chain(self):
        print("Initializing test directory "+self.options.tmpdir)
        initialize_chain_clean(self.options.tmpdir, 4)

    def setup_network(self, split=False):
        args = [
            '-regtestprotectcoinbase',
            '-debug=zrpcunsafe',
            nuparams(NU5_BRANCH_ID, self.nu5_activation),
            '-allowdeprecated=z_getnewaddress',
            '-allowdeprecated=z_getbalance',
            '-debug=mempool',
        ]
        self.nodes = []
        self.nodes.append(start_node(0, self.options.tmpdir, args))
        self.nodes.append(start_node(1, self.options.tmpdir, args))
        self.nodes.append(start_node(2, self.options.tmpdir, args))
        connect_nodes_bi(self.nodes,0,1)
        connect_nodes_bi(self.nodes,1,2)
        connect_nodes_bi(self.nodes,0,2)
        self.is_network_split=False
        self.sync_all()

    def run_test (self):
        print("Mining blocks...")

        self.nodes[0].generate(1)
        self.nodes[0].generate(4)
        self.sync_all()
        walletinfo = self.nodes[0].getwalletinfo()
        assert_equal(Decimal(walletinfo['immature_balance']), Decimal('50'))
        assert_equal(Decimal(walletinfo['balance']), Decimal('0'))
        self.sync_all()
        self.nodes[2].generate(1)
        self.nodes[2].generate(1)
        self.nodes[2].generate(1)
        self.sync_all()
        self.nodes[1].generate(101)
        self.sync_all()
        assert_equal(Decimal(self.nodes[0].getbalance()), Decimal('50'))
        assert_equal(Decimal(self.nodes[1].getbalance()), Decimal('10'))
        assert_equal(Decimal(self.nodes[2].getbalance()), Decimal('30'))

        # create one zaddr that is the target of all shielding
        myzaddr = self.test_init_zaddr(self.nodes[0])

        do_not_shield_taddr = get_coinbase_address(self.nodes[0], 1)

        # Prepare to send taddr->zaddr
        mytaddr = get_coinbase_address(self.nodes[0], 4)

        # Shielding will fail when trying to spend from watch-only address
        self.nodes[2].importaddress(mytaddr)
        try:
            self.nodes[2].z_shieldcoinbase(mytaddr, myzaddr)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal(errorString, "Invalid from address, no payment source found for address.")

        # Shielding will fail because fee is negative
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, -1)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Amount out of range" in errorString, True)

        # Shielding will fail because fee is larger than MAX_MONEY
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, Decimal('21000000.00000001'))
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal(errorString, "Amount out of range")

        # Shielding will fail because limit parameter must be at least 0
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, Decimal('0.001'), -1)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Limit on maximum number of utxos cannot be negative" in errorString, True)

        # Shielding will fail because limit parameter is absurdly large
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, Decimal('0.001'), 99999999999999)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("JSON integer out of range" in errorString, True)

        # Shield coinbase utxos from node 0 of value 40
        fee = conventional_fee(6)
        result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, fee)
        wait_and_assert_operationid_status(self.nodes[0], result['opid'])
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        # Confirm balances and that do_not_shield_taddr containing funds of 10 was left alone
        assert_equal(Decimal(self.nodes[0].getbalance()), Decimal('10'))
        assert_equal(Decimal(self.nodes[0].z_getbalance(do_not_shield_taddr)), Decimal('10'))
        self.test_check_balance_zaddr(self.nodes[0], Decimal('40') - fee)
        assert_equal(Decimal(self.nodes[1].getbalance()), Decimal('20'))
        assert_equal(Decimal(self.nodes[2].getbalance()), Decimal('30'))

        # Shield coinbase utxos from any node 2 taddr
        fee2 = conventional_fee(5)
        result = self.nodes[2].z_shieldcoinbase("*", myzaddr, fee2, None, None, 'AllowLinkingAccountAddresses')
        wait_and_assert_operationid_status(self.nodes[2], result['opid'])
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        assert_equal(Decimal(self.nodes[0].getbalance()), Decimal('10'))
        self.test_check_balance_zaddr(self.nodes[0], Decimal('70') - fee - fee2)
        assert_equal(Decimal(self.nodes[1].getbalance()), Decimal('30'))
        assert_equal(Decimal(self.nodes[2].getbalance()), Decimal('0'))

        # Generate 800 coinbase utxos on node 0, and 20 coinbase utxos on node 2
        self.nodes[0].generate(800)
        self.sync_all()
        self.nodes[2].generate(20)
        self.sync_all()
        self.nodes[1].generate(100)
        self.sync_all()
        mytaddr = get_coinbase_address(self.nodes[0], 800)

        def verify_locking(first, second, limit):
            result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, ZIP_317_FEE, limit)
            assert_equal(Decimal(result["shieldingUTXOs"]), Decimal(first))
            assert_equal(Decimal(result["remainingUTXOs"]), Decimal(second))
            remainingValue = result["remainingValue"]
            opid1 = result['opid']

            # Verify that utxos are locked (not available for selection) by queuing up another shielding operation
            result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, ZIP_317_FEE, 0)
            assert_equal(Decimal(result["shieldingValue"]), Decimal(remainingValue))
            assert_equal(Decimal(result["shieldingUTXOs"]), Decimal(second))
            assert_equal(Decimal(result["remainingValue"]), Decimal('0'))
            assert_equal(Decimal(result["remainingUTXOs"]), Decimal('0'))
            opid2 = result['opid']

            # wait for both async operations to complete
            wait_and_assert_operationid_status(self.nodes[0], opid1)
            wait_and_assert_operationid_status(self.nodes[0], opid2)

        # Shield the 800 utxos over two transactions
        verify_locking('500', '300', 500)

        # sync_all() invokes sync_mempool() but node 2's mempool limit will cause tx1 and tx2 to be rejected.
        # So instead, we sync on blocks and mempool for node 0 and node 1, and after a new block is generated
        # which mines tx1 and tx2, all nodes will have an empty mempool which can then be synced.
        sync_blocks(self.nodes[:2])
        sync_mempools(self.nodes[:2])
        self.nodes[1].generate(1)
        self.sync_all()

        # Verify maximum number of utxos which node 0 can shield is set by default limit parameter of 50
        self.nodes[0].generate(200)
        self.sync_all()
        mytaddr = get_coinbase_address(self.nodes[0], 100)
        result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, ZIP_317_FEE, None, 'DEADBEEF')
        assert_equal(Decimal(result["shieldingUTXOs"]), Decimal('50'))
        assert_equal(Decimal(result["remainingUTXOs"]), Decimal('50'))
        wait_and_assert_operationid_status(self.nodes[0], result['opid'])

        # Verify maximum number of utxos which node 0 can shield can be set by the limit parameter
        result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, ZIP_317_FEE, 33, None)
        assert_equal(Decimal(result["shieldingUTXOs"]), Decimal('33'))
        assert_equal(Decimal(result["remainingUTXOs"]), Decimal('17'))
        wait_and_assert_operationid_status(self.nodes[0], result['opid'])
        # Don't sync node 2 which rejects the tx due to its mempooltxinputlimit
        sync_blocks(self.nodes[:2])
        sync_mempools(self.nodes[:2])
        self.nodes[1].generate(1)
        self.sync_all()

# Note, no "if __name__ == '__main__" and call the test here; it's called from
# pool-specific derived classes in wallet_shieldcoinbase_*.py
