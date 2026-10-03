/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
#include "version.hpp"
#include "frogram.hpp"
#include <iostream>
int main(int argc,char**argv){
 if(argc!=6){std::cerr<<"usage: check-manifest HOST PORT KIND IID GROUP\n";return 2;}
 frogram::Session s(argv[1],std::stoi(argv[2]),LISPER_API); frogram::Memory m(s,LISPER_API);
 std::string v=std::string(argv[3])+"-lengths|"+argv[4]+"|"+argv[5];
 auto cells=m.read("lisp",v);
 for(auto&c:cells)std::cout<<c.instance<<"\n";
 return 0;
}
