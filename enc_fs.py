from __future__ import with_statement
from fuse import FUSE, FuseOSError, Operations
from encryptionstore import retrieve_key
from encryption import encrypt, decrypt, padding_length
import os

from meta_fs import MetaFs

class EncFs(MetaFs):
    enc_keymatter_file = '.enc_keymatter'
    sign_keymatter_file = '.sign_keymatter'
    
    def __init__(self, root, opts):
        MetaFs.__init__(self, root, opts)
        enc_pass = opts['enc_pass']
        sign_pass = opts['sign_pass']

        self.encryption_key = retrieve_key(enc_pass, self._full_path(self.enc_keymatter_file))
        self.signing_key = retrieve_key(sign_pass, self._full_path(self.sign_keymatter_file))
        self.metadata_header_length = 80

        #todo: securely delete passwords
        enc_pass = ''
        sign_pass = ''

    def decrypt_with_metadata(self, path, data):
        print('decrypt path: ' + path)
        metadata = self.read_meta_file(path)
        data = metadata['header'] + data + metadata['padding']
        return decrypt(data, self.encryption_key, self.signing_key)

    #enc_data format: 64 byte digest, 16 byte iv, actual encrypted data, padding on the end
    def write_metadata_file(self, path, enc_data, padlength):
        m_data = {
            'header': enc_data[0:self.metadata_header_length],
            'padding': enc_data[(-1 * padlength):]
        }
        self.write_meta_file(path, m_data)

    def is_key_file(self, partial):
        partial = self._without_leading_slash(partial)
        return partial == self.enc_keymatter_file or partial == self.sign_keymatter_file

    def is_blacklisted_file(self, partial):
        return self.is_key_file(partial) or super(EncFs, self).is_blacklisted_file(partial)

    #blocklength needs to be moved out of this function
    #do not need to read from offset 0 either
    #also need to check whether to append the metadata
    def read(self, path, length, offset, fh):
        if self.is_blacklisted_file(path):
            raise IOError()

        blocklength = 16 #fix this

        os.lseek(fh, 0, os.SEEK_SET)

        readlength = offset + length
        if readlength % blocklength != 0:
            readlength = readlength + blocklength - readlength % blocklength

        data = os.read(fh, readlength)
        if len(data) > 0:
            data = self.decrypt_with_metadata(path, data)
        return data[offset:(offset + length)]

    def write(self, path, buf, offset, fh):
        if self.is_blacklisted_file(path):
            raise IOError

        #compute the entire plaintext to be written to the file
        #currently does not support writing less than the entire file
        plaintext = buf
        try:
            with open(path, 'r') as f:
                data = f.read()

                #prevent useless metadata files. should clean them on deletes / truncates
                if len(data) > 0:
                    data = self.decrypt_with_metadata(path, data)
                    plaintext = data[:offset] + buf + data[(offset + len(buf)):]
        except IOError:
            plaintext = buf
        
        #encrypt and write the metadata file
        filedata = encrypt(plaintext, self.encryption_key, self.signing_key)
        padlength = padding_length(len(plaintext))
        self.write_metadata_file(path, filedata, padlength)

        #write the actual file. The first 80 bytes of filedata are the 
        #hex digest + the iv. The last "padlength" bytes are block padding
        os.lseek(fh, 0, os.SEEK_SET)
        bytes_written = os.write(fh, filedata[self.metadata_header_length:(-1*padlength)])
        return min(len(buf), bytes_written)
