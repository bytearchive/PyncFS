from __future__ import with_statement
from fuse import FUSE, FuseOSError, Operations
from encryptionstore import retrieve_key
import os

from meta_fs import MetaFs
from file_metadata import FileMetaData
from block_cipher import BlockCipher
from util import *

class EncFs(MetaFs):
    enc_keymatter_file = '.enc_keymatter'
    sign_keymatter_file = '.sign_keymatter'

    def __init__(self, root, opts):
        MetaFs.__init__(self, root, opts)
        self.opts = opts
        self.encryption_key = retrieve_key(opts['enc_pass'], self._full_path(self.enc_keymatter_file))
        self.signing_key = retrieve_key(opts['sign_pass'], self._full_path(self.sign_keymatter_file))
        self.cipher = BlockCipher(self.encryption_key, self.signing_key)

        #todo: securely delete passwords
        enc_pass = ''
        sign_pass = ''

    def is_key_file(self, partial):
        partial = self._without_leading_slash(partial)
        return partial == self.enc_keymatter_file or partial == self.sign_keymatter_file

    def is_blacklisted_file(self, partial):
        return self.is_key_file(partial) or super(EncFs, self).is_blacklisted_file(partial)

    # ============
    # File methods
    # ============

    def create(self, path, mode, fi=None):
        f = super(EncFs, self).create(path, mode, fi)
        # Write clear meta here for consistency
        #self.set_empty_meta(path, True)
        with self.with_meta_obj(path) as o:
            o.set_empty(True)

        return f

    # TODO finish metedata updates on truncate
    def truncate(self, path, length, fh=None):
        tmp_size = self.cipher.get_nearest_block_size(length)
        print("truncating encrypted file on block bounds %s" % tmp_size)
        

        self.re_encrypt_file(path, length)

        super(EncFs, self).truncate(path, tmp_size, fh)

        with self.with_meta_obj(path) as o:
            o.set_length(length)

            #if length == 0:
            #    o.set_empty(True)


    def read(self, path, length, offset, fh):
        if self.is_blacklisted_file(path):
            raise IOError()

        metadata = self.get_meta_obj(path)
        return self.cipher.read_file(path, length, offset, fh, metadata)

    def write(self, path, buf, offset, fh):
        if self.is_blacklisted_file(path):
            raise IOError

        print("write %s len: %s offset: %s" % (path, len(buf), offset))

        metadata = self.get_meta_obj(path)
        res = self.cipher.write_file(self._full_path(path), buf, offset, metadata)
        num_written = res[0]
        new_meta = res[1]
        
        self.update_meta_on_write(metadata, new_meta)
        return num_written


    #Hm. Ok. prob (self, path, new_length). I will come back to this
    def update_meta_on_write(self, metadata, new_length):

        # Update len
        metadata.set_length(new_meta['length'])
        metadata.update(new_meta)

        # Check empty
        if metadata['length'] > 0:
            metadata['empty'] = False

        # Save
        self.save_meta_obj(metadata)

    def re_encrypt_file(self, path, length):
        print("re encrypt %s to length %s" % (path, length))
        metadata = self.get_meta_obj(path)
        print(metadata)
        #with os.open(self._full_path(path), os.O_RDONLY) as fh:
        fh = os.open(self._full_path(path), os.O_RDWR)
        os.lseek(fh, 0, os.SEEK_SET)
        data = self.cipher.read_file(path, length, 0, fh, metadata)

        # Trim to new length
        new_data = data[:length]

        # Pad with zero if needed
        if metadata['length'] < length:
            dif = length - metadata['length']
            new_data += ''.join([chr(0)] * dif)

        os.ftruncate(fh, 0)
        os.lseek(fh, 0, os.SEEK_SET)

        #Write new encrypted
        enc_block_res = self.cipher.encrypt_data(new_data)
        enc_data = enc_block_res[0]
        new_meta = enc_block_res[1]

        # Save back
        write_data = enc_data[self.cipher.metadata_header_length:(-1*new_meta['pad_len'])]
        os.write(fh, write_data)

        os.close(fh)

        self.update_meta_on_write(metadata, new_meta)
        # print_bytes(new_data)
        # print_bytes(write_data)
