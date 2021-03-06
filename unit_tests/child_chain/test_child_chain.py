from threading import Thread

import pytest
import rlp
from ethereum import utils as eth_utils
from mockito import ANY, expect, mock, verify, when

from plasma_cash.child_chain.block import Block
from plasma_cash.child_chain.child_chain import ChildChain
from plasma_cash.child_chain.exceptions import (InvalidBlockNumException,
                                                InvalidBlockSignatureException,
                                                InvalidTxSignatureException,
                                                PreviousTxNotFoundException,
                                                TxAlreadySpentException,
                                                TxAmountMismatchException,
                                                TxWithSameUidAlreadyExists)
from plasma_cash.child_chain.transaction import Transaction
from plasma_cash.utils.db.memory_db import MemoryDb
from unit_tests.unstub_mixin import UnstubMixin


class TestChildChain(UnstubMixin):
    DUMMY_AUTHORITY = b"\x14\x7f\x08\x1b\x1a6\xa8\r\xf0Y\x15(ND'\xc1\xf6\xdd\x98\x84"
    DUMMY_SIG = '01' * 65  # sig for DUMMY_AUTHORITY
    DUMMY_TX_NEW_OWNER = b'\xfd\x02\xec\xeeby~u\xd8k\xcf\xf1d.\xb0\x84J\xfb(\xc7'

    @pytest.fixture(scope='function')
    def db(self):
        return MemoryDb()

    @pytest.fixture(scope='function')
    def root_chain(self):
        root_chain = mock()
        root_chain.functions = mock()
        return root_chain

    @pytest.fixture(scope='function')
    def child_chain(self, root_chain, db):
        DUMMY_TX_OWNER = b'\x8cT\xa4\xa0\x17\x9f$\x80\x1fI\xf92-\xab<\x87\xeb\x19L\x9b'

        expect(root_chain).eventFilter('Deposit', {'fromBlock': 0})
        expect(Thread).start()
        child_chain = ChildChain(self.DUMMY_AUTHORITY, root_chain, db)

        # create a dummy transaction
        tx = Transaction(prev_block=0, uid=1, amount=10, new_owner=DUMMY_TX_OWNER)

        # create a block with the dummy transaction
        db.save_block(Block([tx]), 1)
        child_chain.current_block_number = 2
        db.increment_current_block_num()
        return child_chain

    def test_constructor(self, root_chain, db):
        expect(root_chain).eventFilter('Deposit', {'fromBlock': 0})
        expect(Thread).start()

        ChildChain(self.DUMMY_AUTHORITY, root_chain, db)

    def test_apply_deposit(self, child_chain):
        DUMMY_AMOUNT = 123
        DUMMY_UID = 'dummy uid'
        DUMMY_ADDR = b'\xfd\x02\xec\xeeby~u\xd8k\xcf\xf1d.\xb0\x84J\xfb(\xc7'

        event = {'args': {
            'amount': DUMMY_AMOUNT,
            'uid': DUMMY_UID,
            'depositor': DUMMY_ADDR,
        }}

        child_chain.apply_deposit(event)

        tx = child_chain.current_block.transaction_set[0]
        assert tx.amount == DUMMY_AMOUNT
        assert tx.uid == DUMMY_UID
        assert tx.new_owner == eth_utils.normalize_address(DUMMY_ADDR)

    def test_submit_block(self, child_chain, root_chain):
        DUMMY_MERKLE = 'merkle hash'
        MOCK_TRANSACT = mock()

        block_number = child_chain.current_block_number
        block = child_chain.current_block
        when(child_chain.current_block).merklize_transaction_set().thenReturn(DUMMY_MERKLE)
        (when(root_chain.functions)
            .submitBlock(DUMMY_MERKLE, block_number)
            .thenReturn(MOCK_TRANSACT))

        child_chain.submit_block(self.DUMMY_SIG)

        verify(MOCK_TRANSACT).transact(ANY)
        assert child_chain.current_block_number == block_number + 1
        assert child_chain.db.get_block(block_number) == block
        assert child_chain.current_block == Block()

    def test_submit_block_with_invalid_sig(self, child_chain):
        INVALID_SIG = '11' * 65
        with pytest.raises(InvalidBlockSignatureException):
            child_chain.submit_block(INVALID_SIG)

    def test_submit_block_with_empty_sig(self, child_chain):
        EMPTY_SIG = '00' * 65
        with pytest.raises(InvalidBlockSignatureException):
            child_chain.submit_block(EMPTY_SIG)

    def test_apply_transaction(self, child_chain):
        DUMMY_TX_KEY = b'8b76243a95f959bf101248474e6bdacdedc8ad995d287c24616a41bd51642965'

        tx = Transaction(prev_block=1, uid=1, amount=10, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_TX_KEY))

        child_chain.apply_transaction(rlp.encode(tx).hex())

        prev_tx = child_chain.db.get_block(1).transaction_set[0]
        assert child_chain.current_block.transaction_set[0] == tx
        assert prev_tx.new_owner == tx.sender
        assert prev_tx.amount == tx.amount
        assert prev_tx.spent is True

    def test_apply_transaction_with_previous_tx_not_exist_should_fail(self, child_chain):
        DUMMY_TX_KEY = b'8b76243a95f959bf101248474e6bdacdedc8ad995d287c24616a41bd51642965'

        # token with uid 3 doesn't exist
        tx = Transaction(prev_block=1, uid=3, amount=10, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_TX_KEY))

        with pytest.raises(PreviousTxNotFoundException):
            child_chain.apply_transaction(rlp.encode(tx).hex())

    def test_apply_transaction_with_double_spending_should_fail(self, child_chain):
        DUMMY_TX_KEY = b'8b76243a95f959bf101248474e6bdacdedc8ad995d287c24616a41bd51642965'

        tx = Transaction(prev_block=1, uid=1, amount=10, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_TX_KEY))

        child_chain.apply_transaction(rlp.encode(tx).hex())

        # try to spend a spent transaction
        with pytest.raises(TxAlreadySpentException):
            child_chain.apply_transaction(rlp.encode(tx).hex())

    def test_apply_transaction_with_mismatch_amount_should_fail(self, child_chain):
        DUMMY_TX_KEY = b'8b76243a95f959bf101248474e6bdacdedc8ad995d287c24616a41bd51642965'

        # token with uid 1 doesn't have 20
        tx = Transaction(prev_block=1, uid=1, amount=20, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_TX_KEY))

        with pytest.raises(TxAmountMismatchException):
            child_chain.apply_transaction(rlp.encode(tx).hex())

    def test_apply_transaction_with_invalid_sig_should_fail(self, child_chain):
        DUMMY_INVALID_TX_KEY = b'7a76243a95f959bf101248474e6bdacdedc8ad995d287c24616a41bd51642965'

        tx = Transaction(prev_block=1, uid=1, amount=10, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_INVALID_TX_KEY))

        with pytest.raises(InvalidTxSignatureException):
            child_chain.apply_transaction(rlp.encode(tx).hex())

    def test_apply_transaction_with_same_uid_tx_already_in_block_should_fail(self, child_chain):
        # create a another (invalid) transaction with same uid in block 2
        # this transaction would be used as the prev_tx of second same uid tx
        DUMMY_TX_OWNER = b'\x8cT\xa4\xa0\x17\x9f$\x80\x1fI\xf92-\xab<\x87\xeb\x19L\x9b'
        tx = Transaction(prev_block=0, uid=1, amount=10, new_owner=DUMMY_TX_OWNER)
        child_chain.db.save_block(Block([tx]), 2)
        child_chain.current_block_number = 3
        child_chain.db.increment_current_block_num()

        # first apply a tx with the uid
        DUMMY_TX_KEY = b'8b76243a95f959bf101248474e6bdacdedc8ad995d287c24616a41bd51642965'
        tx = Transaction(prev_block=1, uid=1, amount=10, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_TX_KEY))
        child_chain.apply_transaction(rlp.encode(tx).hex())

        # apply another tx with the same uid should fail
        tx = Transaction(prev_block=2, uid=1, amount=10, new_owner=self.DUMMY_TX_NEW_OWNER)
        tx.sign(eth_utils.normalize_key(DUMMY_TX_KEY))
        with pytest.raises(TxWithSameUidAlreadyExists):
            child_chain.apply_transaction(rlp.encode(tx).hex())

    def test_get_current_block(self, child_chain):
        expected = rlp.encode(child_chain.current_block).hex()
        assert expected == child_chain.get_current_block()

    def test_get_block(self, child_chain):
        DUMMY_BLK_NUM = 1

        expected = rlp.encode(child_chain.db.get_block(DUMMY_BLK_NUM)).hex()
        assert expected == child_chain.get_block(DUMMY_BLK_NUM)

    def test_get_block_with_current_block_number(self, child_chain):
        current_block_number = child_chain.current_block_number
        expected = rlp.encode(child_chain.current_block).hex()
        assert expected == child_chain.get_block(current_block_number)

    def test_get_non_existing_block_would_fail(self, child_chain):
        NON_EXISTING_BLOCK_NUM = 10000
        with pytest.raises(InvalidBlockNumException):
            child_chain.get_block(NON_EXISTING_BLOCK_NUM)

    def test_get_block_with_less_than_1_would_fail(self, child_chain):
        with pytest.raises(InvalidBlockNumException):
            child_chain.get_block(0)

    def test_get_proof(self, child_chain):
        DUMMY_BLK_NUM = 1
        DUMMY_UID = 1

        block = child_chain.db.get_block(DUMMY_BLK_NUM)
        block.merklize_transaction_set()
        expected_proof = block.merkle.create_merkle_proof(DUMMY_UID)
        assert expected_proof == child_chain.get_proof(DUMMY_BLK_NUM, DUMMY_UID)
